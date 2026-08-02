"""RENDER — runs the visual toolbox (§7) for APPROVED posts.

Order of operations per post:
  1. platform pre-validation (caption rules) BEFORE any spend
  2. BANK-FIRST asset matching against the `assets` table
  3. model tool routing (validated; deterministic fallback)
  4. execute the chain (download bank bytes → fal → local framing)
  5. upload the result to Drive `_generated/{week}/{post_id}.{ext}` (the canonical home;
     the fal URL is only a fallback), persist genome + media ids, status=RENDERED
  6. any toolbox failure → PARKED with a structured wishlist — never a weak visual
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.integrations.upload_post_client import PlatformValidationError, validate_for_platform
from app.models import Post
from app.toolbox.asset_match import find_candidates, mark_used
from app.toolbox.edit_combine import (
    edit_images,
    edit_video,
    fit_image_aspect,
    trim_clip,
)
from app.toolbox.enhance import enhance_image
from app.toolbox.generate import generate_image
from app.toolbox.park import park_post
from app.toolbox.selector import select_tools
from app.toolbox.text_card import next_pairing, render_text_card

# Genome subjects the router may ask the bank for, in preference order per angle keywords.
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
        # Email copy is structured; stored as JSON in posts.caption (DECISIONS.md #15).
        return json.dumps(data, ensure_ascii=False)
    caption = data.get("caption") if isinstance(data, dict) else None
    if not caption:
        raise RenderFailure("caption prompt returned no caption")
    return caption


def _execute_plan(session: Session, plan, post: Post, media_spec: dict, drive, fal, cfg) -> tuple[str, str]:
    """Run the ordered tool chain; returns (local_path, extension)."""
    endpoints = cfg["image_endpoints"]
    family = cfg["video_family"]
    media_kind = media_spec["media"]
    aspect = media_spec["aspect"]
    chosen = []
    if plan.asset_ids:
        from app.models import Asset

        chosen = [session.get(Asset, aid) for aid in plan.asset_ids]
        chosen = [c for c in chosen if c is not None]

    local: str | None = None
    public_url: str | None = None

    for tool in plan.tools:
        if tool == "asset":
            if not chosen:
                raise RenderFailure("plan selected 'asset' but no candidate resolved")
            suffix = ".mp4" if chosen[0].medium == "video" else ".jpg"
            local = drive.download(chosen[0].drive_file_id, suffix=suffix)
        elif tool == "edit_combine":
            if media_kind == "video":
                urls = []
                for c in chosen:
                    src = drive.download(c.drive_file_id, suffix=".mp4")
                    src = trim_clip(src, max_s=family.get("duration_range_s", [4, 15])[1])
                    urls.append(fal.upload_public(src))
                local = edit_video(
                    fal, family, plan.prompt or (post.hook or ""), urls,
                    duration_s=media_spec.get("duration_s", 12),
                    aspect_ratio=media_spec.get("aspect_ratio", "9:16"),
                    resolution=media_spec.get("resolution", "720p"),
                )
            else:
                urls = []
                for c in chosen:
                    src = drive.download(c.drive_file_id, suffix=".jpg")
                    urls.append(fal.upload_public(src))
                if local and not urls:
                    urls = [fal.upload_public(local)]
                local = edit_images(fal, endpoints, plan.prompt or (post.hook or ""), urls)
        elif tool == "generate":
            local = generate_image(fal, plan.prompt or (post.hook or ""), plan.subject,
                                   cfg["loras"], aspect, endpoints["lora"])
        elif tool == "enhance":
            if local is None:
                raise RenderFailure("ENHANCE reached with no prior image in the chain")
            public_url = fal.upload_public(local)
            local = enhance_image(fal, endpoints, public_url, "photo" if media_kind == "photo" else media_kind)
        elif tool == "text_card":
            pairing = next_pairing(session)
            local = render_text_card(post.hook or post.angle or "Artec blocks",
                                     pairing, cfg["fonts"], aspect=aspect)
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


def render(session: Session, llm, drive, fal, post_ids: list[str] | None = None,
           all_approved: bool = False, log=print) -> dict:
    cfg_keys = ("image_endpoints", "video_family", "loras", "fonts", "channel_media",
                "allow_person_assets", "text_card_pairings")
    cfg = {k: get_config(session, k) for k in cfg_keys}

    q = select(Post).where(Post.status == "APPROVED").order_by(Post.post_id)
    posts = [p for p in session.execute(q).scalars()
             if all_approved or (post_ids and p.post_id in post_ids)]
    if not posts:
        log("render: no APPROVED posts selected")
        return {"rendered": 0, "parked": 0}

    rendered = parked = 0
    for post in posts:
        media_spec = cfg["channel_media"].get(post.channel)
        if media_spec is None:
            log(f"{post.post_id}: unknown channel {post.channel} — skipped")
            continue
        try:
            caption = _caption_for(llm, post, media_spec)
            if post.channel != "email":
                # Money-guard: validate BEFORE any render spend.
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
                raise RenderFailure("no tool chain can hit the match")

            local, ext = _execute_plan(session, plan, post, media_spec, drive, fal, cfg)

            drive_file_id = drive.upload_generated(local, str(post.week_start), f"{post.post_id}.{ext}")
            post.media_drive_file_id = drive_file_id
            post.visual_tools = plan.tools
            post.status = "RENDERED"
            session.flush()
            rendered += 1
            log(f"{post.post_id}: rendered via {plan.tools} → drive:{drive_file_id}")
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
    return {"rendered": rendered, "parked": parked}
