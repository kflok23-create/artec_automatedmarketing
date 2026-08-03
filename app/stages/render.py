"""RENDER v3 — Python first, bank-only for the product, budgeted model calls.

Order of operations per post:
  1. platform pre-validation (caption rules) BEFORE any spend
  2. BANK-ONLY asset matching against the `assets` table
  3. tool routing (validated; deterministic fallback; no generate tool exists)
  4. execute the chain — Pillow/ffmpeg primary; the one surviving model call (enhance
     upscale) passes the text guard and the budget on every call
  5. upload to Drive `_generated/{week}/{post_id}.{ext}`, persist genome, status=RENDERED
  6. no chain hits the match → PARKED with a structured wishlist — never generation
  7. budget cap reached → the current AND all remaining posts PARK with a wishlist entry;
     the run never proceeds past the cap (v3 Rule 4)
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.integrations.upload_post_client import PlatformValidationError, validate_for_platform
from app.models import Post
from app.toolbox.asset_match import find_candidates, mark_used
from app.toolbox.budget import GuardedFal, RenderBudget, RunBudgetExceeded
from app.toolbox.edit_combine import edit_video_pipeline, fit_image_aspect
from app.toolbox.enhance import enhance_image
from app.toolbox.overlay import overlay_caption
from app.toolbox.park import park_post
from app.toolbox.selector import select_tools
from app.toolbox.text_card import FONTS_DIR, next_pairing, render_text_card

DEFAULT_SUBJECT_BY_MEDIA = {"photo": "assembled_blocks", "video": "assembled_blocks"}


class RenderFailure(RuntimeError):
    pass


def _caption_for(llm, post: Post, media_spec: dict) -> str:
    data = llm.complete_json(
        "caption_v1.md",
        {
            "channel": post.channel,
            "angle": post.angle or "",
            "hook": post.hook or "",
            "cta_type": post.cta_type or "",
            "cta_placement": post.cta_placement or "",
            "keywords": post.keywords or [],
            "tracked_url": post.tracked_url or "",
            "code": post.code or "",
        },
    )
    if post.channel == "email":
        return json.dumps(data, ensure_ascii=False)
    caption = data.get("caption") if isinstance(data, dict) else None
    if not caption:
        raise RenderFailure("caption prompt returned no caption")
    return caption


def _execute_plan(session: Session, plan, post: Post, media_spec: dict, drive, gfal, cfg) -> tuple[str, str]:
    """Run the ordered tool chain; returns (local_path, extension). gfal is the GuardedFal
    — text guard + budget on every model call."""
    media_kind = media_spec["media"]
    aspect = media_spec["aspect"]
    fonts = cfg["fonts"]
    chosen = []
    if plan.asset_ids:
        from app.models import Asset

        chosen = [session.get(Asset, aid) for aid in plan.asset_ids]
        chosen = [c for c in chosen if c is not None]

    local: str | None = None

    for tool in plan.tools:
        if tool == "asset":
            if not chosen:
                raise RenderFailure("plan selected 'asset' but no candidate resolved")
            suffix = ".mp4" if chosen[0].medium == "video" else ".jpg"
            local = drive.download(chosen[0].drive_file_id, suffix=suffix)
        elif tool == "video_edit":
            if local is None:
                raise RenderFailure("video_edit reached with no source clip in the chain")
            # The first-class v3 video path: ffmpeg only, zero model calls.
            local = edit_video_pipeline(
                local,
                duration_s=media_spec.get("duration_s", 12),
                aspect_ratio=media_spec.get("aspect_ratio", "9:16"),
                caption=post.hook,
                font_path=str(FONTS_DIR / fonts["display"]),
            )
        elif tool == "enhance":
            if local is None:
                raise RenderFailure("enhance reached with no prior image in the chain")
            local = enhance_image(gfal, cfg["model_endpoints"], cfg["enhance_whitelist"],
                                  "upscale", local, "photo" if media_kind == "photo" else media_kind)
        elif tool == "overlay":
            if local is None:
                raise RenderFailure("overlay reached with no prior image in the chain")
            local = overlay_caption(local, post.hook or "", fonts)
        elif tool == "text_card":
            pairing = next_pairing(session)
            local = render_text_card(post.hook or post.angle or "Artec blocks",
                                     pairing, fonts, aspect=aspect)
        else:
            raise RenderFailure(f"unknown tool {tool!r}")

    if local is None:
        raise RenderFailure("tool chain produced no output")

    for c in chosen:
        mark_used(session, c)
    post.source_asset_ids = [c.drive_file_id for c in chosen]

    if media_kind == "photo":
        local = fit_image_aspect(local, aspect)
        return local, "jpg"
    return local, "mp4"


def _budget_wishlist(media_spec: dict) -> list[dict]:
    folder = "raw-video/assembled" if media_spec["media"] == "video" else "raw-photo/assembled"
    return [{
        "target_folder": folder,
        "medium": media_spec["media"],
        "aspect": media_spec["aspect"],
        "description": "render budget cap reached this run — no new asset needed; re-run "
                       "render next cycle and this post renders first",
    }]


def render(session: Session, llm, drive, fal, post_ids: list[str] | None = None,
           all_approved: bool = False, log=print, recorder=None) -> dict:
    cfg_keys = ("model_endpoints", "enhance_whitelist", "loras", "fonts", "channel_media",
                "allow_person_assets", "text_card_pairings", "render_run_cap_cents",
                "per_call_ceiling_cents", "max_output_megapixels")
    cfg = {k: get_config(session, k) for k in cfg_keys}
    # Prices come from the endpoint_prices TABLE (unit-aware), not config — see §7·C5.
    budget = RenderBudget(session, cfg["render_run_cap_cents"], cfg["per_call_ceiling_cents"],
                          max_output_megapixels=cfg["max_output_megapixels"], log=log)
    gfal = GuardedFal(fal, budget)

    q = select(Post).where(Post.status == "APPROVED").order_by(Post.post_id)
    posts = [p for p in session.execute(q).scalars()
             if all_approved or (post_ids and p.post_id in post_ids)]
    if not posts:
        log("render: no APPROVED posts selected")
        return {"rendered": 0, "parked": 0, "spent_cents": 0}

    rendered = parked = 0
    budget_exhausted = False
    for post in posts:
        media_spec = cfg["channel_media"].get(post.channel)
        if media_spec is None:
            log(f"{post.post_id}: unknown channel {post.channel} — skipped")
            continue
        if budget_exhausted:
            park_post(session, post, reason="render budget cap reached earlier in this run",
                      wishlist=_budget_wishlist(media_spec))
            parked += 1
            log(f"{post.post_id}: PARKED — budget cap reached earlier in this run")
            continue
        try:
            caption = _caption_for(llm, post, media_spec)
            if post.channel != "email":
                full_caption = f"{caption}\n{post.tracked_url}"
                validate_for_platform(post.channel, media_spec["media"], full_caption,
                                      duration_s=media_spec.get("duration_s"))
            post.caption = caption

            candidates = find_candidates(
                session,
                subject=DEFAULT_SUBJECT_BY_MEDIA[media_spec["media"]],
                medium=media_spec["media"],
                aspect=media_spec["aspect"],
                allow_person=bool(cfg["allow_person_assets"]),
            )
            genome = {
                "post_id": post.post_id, "channel": post.channel, "angle": post.angle,
                "hook": post.hook, "cta_type": post.cta_type, "keywords": post.keywords,
            }
            plan = select_tools(llm, genome, candidates, media_spec["media"],
                                bool(cfg["allow_person_assets"]))
            if plan is None:
                raise RenderFailure("no bank asset and no Python path hits the match")

            local, ext = _execute_plan(session, plan, post, media_spec, drive, gfal, cfg)

            drive_file_id = drive.upload_generated(local, str(post.week_start), f"{post.post_id}.{ext}")
            post.media_drive_file_id = drive_file_id
            post.visual_tools = plan.tools
            post.status = "RENDERED"
            session.flush()
            rendered += 1
            log(f"{post.post_id}: rendered via {plan.tools} → drive:{drive_file_id}")
        except RunBudgetExceeded as e:
            # v3 Rule 4: refuse the call, park this AND everything after it.
            budget_exhausted = True
            park_post(session, post, reason=str(e)[:500], wishlist=_budget_wishlist(media_spec))
            parked += 1
            log(f"{post.post_id}: PARKED — {e}")
        except PlatformValidationError as e:
            log(f"{post.post_id}: platform validation failed pre-spend — {e}")
            post.status = "FAILED"
            post.park_reason = str(e)[:500]
            session.flush()
        except Exception as e:
            wishlist = None
            try:
                wishlist = llm.complete_json(
                    "wishlist_v1.md",
                    {"genome": {"channel": post.channel, "angle": post.angle, "hook": post.hook},
                     "media_kind": media_spec["media"], "aspect": media_spec["aspect"]},
                )
            except Exception:
                pass
            if not isinstance(wishlist, list) or not wishlist:
                folder = "raw-video" if media_spec["media"] == "video" else "raw-photo/assembled"
                wishlist = [{
                    "target_folder": folder,
                    "medium": media_spec["media"],
                    "aspect": media_spec["aspect"],
                    "description": f"asset for: {post.hook or post.angle or post.post_id}",
                }]
            try:
                park_post(session, post, reason=f"{type(e).__name__}: {e}"[:500], wishlist=wishlist)
                parked += 1
                log(f"{post.post_id}: PARKED — {type(e).__name__}: {e}")
            except Exception as park_err:
                post.status = "FAILED"
                post.park_reason = f"{type(e).__name__}: {e}"[:500]
                session.flush()
                log(f"{post.post_id}: FAILED (park also failed: {park_err})")
    # Record the run's fal spend as a NUMBER so the digest can query week-to-date spend
    # rather than parse it out of log text.
    if recorder is not None:
        recorder.cost_micros = budget.spent_micros
    log(f"render: spent {budget.spent_cents:.2f}¢ of {budget.run_cap_cents:.0f}¢ this run")
    return {"rendered": rendered, "parked": parked, "spent_cents": budget.spent_cents}
