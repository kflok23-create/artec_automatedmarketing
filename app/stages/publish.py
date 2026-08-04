"""PUBLISH — Upload-Post (five social surfaces) + Brevo campaign (consumer list only).

Hard rules:
- A post that already has external_post_id REFUSES to publish (double-publishing is the
  most expensive possible bug — §9.4).
- CHECKPOINT 4: the FIRST publish on a fresh install prints exactly what is about to go
  live and requires confirmation; persisted in config so it fires once per install ever.
- Media is downloaded from Drive and streamed as bytes; the Brevo hero image is uploaded to
  fal storage for a public URL (Drive links are never public media).
- A Brevo 402 leaves the post RENDERED for retry.
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config, set_config
from app.integrations.brevo_client import BrevoCreditsError, substitute_template
from app.integrations.upload_post_client import (  # noqa: F401
    PAGE_TARGETED_PLATFORMS,
    extract_external_id,
    page_targets,
    verify_publish_target,
)
from app.models import Post


class DoublePublishError(RuntimeError):
    pass


class FirstPublishNotConfirmed(RuntimeError):
    pass


def _first_publish_preview(session: Session, post: Post, brevo) -> str:
    caption = post.caption or ""
    caption_preview = caption if len(caption) <= 280 else (
        f"{caption[:280]}… [preview truncated — full caption is {len(caption)} chars and publishes in full]"
    )
    lines = [
        "CHECKPOINT 4 — FIRST LIVE PUBLISH on this install",
        f"  post_id:     {post.post_id}",
        f"  channel:     {post.channel}",
        f"  caption:     {caption_preview}",
        f"  media:       drive:{post.media_drive_file_id or '-'} (fallback {post.media_url or '-'})",
        f"  tracked URL: {post.tracked_url}",
    ]
    if post.channel == "email":
        try:
            lines.append(f"  Brevo list recipients: {brevo.get_list_count()}")
        except Exception as e:
            lines.append(f"  Brevo list recipients: <unavailable: {type(e).__name__}>")
    return "\n".join(lines)


def publish_email(session: Session, post: Post, brevo, fal, drive) -> str:
    """Brevo flow (§9.2): fetch template HTML → substitute six in-body variables →
    create classic campaign (htmlContent, never templateId) → sendNow."""
    try:
        copy = json.loads(post.caption or "{}")
    except json.JSONDecodeError:
        copy = {}
    required = ("subject", "headline", "body_copy", "cta_text", "story_block")
    missing = [k for k in required if not copy.get(k)]
    if missing:
        raise RuntimeError(f"email copy missing fields {missing} — re-run render for {post.post_id}")

    local = drive.download(post.media_drive_file_id, suffix=".jpg")
    hero_url = fal.upload_public(local)  # public host; Drive links are viewer pages

    html = brevo.get_template_html()
    substituted = substitute_template(
        html,
        {
            "hero_image_url": hero_url,
            "headline": copy["headline"],
            "body_copy": copy["body_copy"],
            "cta_text": copy["cta_text"],
            "tracked_url": post.tracked_url or "",
            "story_block": copy["story_block"],
        },
    )
    # subject is the SEVENTH variable — a campaign parameter, never substituted into the body.
    campaign_id = brevo.create_campaign(name=post.post_id, subject=copy["subject"], html=substituted)
    brevo.send_now(campaign_id)
    return str(campaign_id)


# ---------------------------------------------------------------------------------------
# v4 §B — THE TWO SKIP RULES. Asserted as rules, not left as an ordering accident: both are
# evaluated for every post on every publish pass, before anything else happens.
#
#   1. An email post NEVER publishes unless status is APPROVED_TO_SEND. Email is the only
#      irreversible surface — once sent it reaches every subscriber and cannot be recalled.
#   2. A post carrying VIDEO never publishes unless status is APPROVED_TO_SEND, because
#      nobody may approve a video they were never shown.
#
# Status alone is not the whole gate: the recorded review is checked too. A status can be
# reached by a route the review never took; a receipt cannot.
# ---------------------------------------------------------------------------------------

PUBLISHABLE_STATUSES = ("RENDERED", "APPROVED_TO_SEND")


def carries_video(session: Session, post: Post) -> bool:
    spec = (get_config(session, "channel_media", {}) or {}).get(post.channel, {})
    return spec.get("media") == "video" or (post.media_drive_file_id or "").endswith(".mp4")


def skip_reason(session: Session, post: Post) -> str | None:
    """Why this post must NOT go out on this pass, or None. Pure and directly testable."""
    if post.channel == "email":
        if post.status != "APPROVED_TO_SEND":
            return ("email never auto-publishes: it is the only irreversible surface, so it "
                    f"requires an email review approval (status is {post.status})")
        decision = (post.email_review or {}).get("decision")
        if decision != "approve":
            return (f"email review decision is {decision!r}, not 'approve' — no code path "
                    "sends an email without a recorded approval")
    if carries_video(session, post):
        if post.status != "APPROVED_TO_SEND":
            return ("video never auto-publishes: it requires a video review approval "
                    f"(status is {post.status})")
        review = post.video_review or {}
        if not review.get("telegram_message_id"):
            return ("video has no delivery receipt — nobody approves a video they were "
                    "never shown")
        if review.get("decision") != "approve":
            return f"video review decision is {review.get('decision')!r}, not 'approve'"
    return None


def run_preflight(session: Session, drive, post: Post, log=print) -> bool:
    """§5.3 — mandatory on every asset, blocking. The first place A is load-bearing.
    A failure PARKS with a wishlist entry: a human is never shown a broken file."""
    from app.stages.preflight import preflight, preflight_wishlist

    spec = (get_config(session, "channel_media", {}) or {}).get(post.channel, {})
    kind = spec.get("media", "photo")
    local = publish_media_path(session, drive, post)
    duration = float(spec.get("duration_s", 12) or 12)
    result = preflight(
        local, media_kind=kind, aspect=spec.get("aspect", "square"),
        aspect_ratio=spec.get("aspect_ratio", "9:16"),
        duration_bounds=(max(1.0, duration * 0.5), duration * 2.0),
        stored_caption=post.caption, rendered_caption=post.caption,
    )
    if result.ok:
        return True
    post.status = "PARKED"
    post.park_reason = f"publish pre-flight: {'; '.join(result.failures)}"[:500]
    post.asset_wishlist = preflight_wishlist(kind, spec.get("aspect", "square"),
                                             result.failures)
    session.flush()
    log(f"{post.post_id}: PARKED by publish pre-flight — {'; '.join(result.failures)}")
    return False


def publish_media_path(session: Session, drive, post: Post) -> str:
    """THE bytes that go live. Every surface that shows the operator a file must read it
    through here — deliver_video included — so approving one artefact and publishing a
    different one is not expressible."""
    spec = (get_config(session, "channel_media", {}) or {}).get(post.channel, {})
    suffix = ".mp4" if spec.get("media") == "video" else ".jpg"
    if not post.media_drive_file_id:
        raise MediaNotInDrive(
            f"{post.post_id} has no media_drive_file_id — §7.9 requires every render output "
            "in Drive _generated/; there is no fal-URL fallback, because a silent fallback "
            "is how the operator ends up approving a different file from the one that ships"
        )
    return drive.download(post.media_drive_file_id, suffix=suffix)


class MediaNotInDrive(RuntimeError):
    """The post's bytes are not where publish reads them from."""


def publish(session: Session, drive, fal, uploader, brevo,
            post_ids: list[str] | None = None, all_rendered: bool = False,
            confirm: bool = True, log=print) -> dict:
    q = select(Post).where(Post.status.in_(PUBLISHABLE_STATUSES)).order_by(Post.post_id)
    selected = [p for p in session.execute(q).scalars()
                if all_rendered or (post_ids and p.post_id in post_ids)]

    posts, skipped = [], 0
    for post in selected:
        reason = skip_reason(session, post)
        if reason:
            skipped += 1
            log(f"{post.post_id}: SKIPPED — {reason}")
            continue
        posts.append(post)

    if not posts:
        log(f"publish: nothing to publish ({skipped} skipped by the review gates)")
        return {"published": 0, "skipped": skipped}

    published = 0
    for post in posts:
        if post.external_post_id:
            raise DoublePublishError(
                f"{post.post_id} already has external_post_id={post.external_post_id} — "
                "refusing to publish twice"
            )

        if bool(get_config(session, "confirm_first_publish", True)):
            preview = _first_publish_preview(session, post, brevo)
            log(preview)
            if not confirm:
                raise FirstPublishNotConfirmed(
                    "first publish requires confirmation — re-run `artec publish` and "
                    "answer 'continue', or pass --yes"
                )
            answer = input("Type 'continue' to go live (anything else aborts): ").strip().lower()
            if answer != "continue":
                raise FirstPublishNotConfirmed("operator did not confirm the first publish")
            set_config(session, "confirm_first_publish", False)  # once per install, never again

        try:
            if post.channel == "email":
                external_id = publish_email(session, post, brevo, fal, drive)
            else:
                # §5.3 pre-flight is mandatory and BLOCKING — it runs before the upload,
                # not after, so a broken file parks instead of shipping.
                if not run_preflight(session, drive, post, log=log):
                    continue
                # channel_media decides the media kind — the source of truth for surfaces.
                media_spec = get_config(session, "channel_media").get(post.channel, {})
                kind = media_spec.get("media", "photo")
                local = publish_media_path(session, drive, post)
                title = f"{post.caption}\n{post.tracked_url}"
                # PAGE TARGETING. facebook and linkedin publish AS A PAGE; the ids come
                # from config and `_page_fields` REFUSES if one is missing rather than
                # letting the post fall through to the personal profile of the account
                # that OAuth'd. Omitting the parameter returns SUCCESS from Upload-Post,
                # so nothing downstream would have noticed.
                targets = page_targets(session)
                if post.channel in PAGE_TARGETED_PLATFORMS:
                    log(f"{post.post_id}: TARGETING {post.channel} page "
                        f"{targets.get(post.channel)!r}")
                if kind == "video":
                    resp = uploader.upload_video(post.channel, local, title,
                                                 page_targets=targets)
                else:
                    resp = uploader.upload_photo(post.channel, local, title,
                                                 page_targets=targets)
                external_id = extract_external_id(resp)
                # ITEM 6 — post-publish verification, two sides from two sources: the id we
                # ASKED for (config) and the owner the response REPORTS. A wrong target is
                # caught on its first occurrence rather than at the first missing metric,
                # weeks later. Never fatal: the post is already public, and raising here
                # would leave it PUBLISHED-but-unrecorded, which is worse.
                mismatch = verify_publish_target(post.channel, targets, resp)
                if mismatch:
                    log(f"{post.post_id}: !! TARGET MISMATCH — {mismatch}")
                    post.park_reason = f"target mismatch: {mismatch}"
            post.external_post_id = external_id
            post.posted_at = datetime.now(UTC)
            post.status = "PUBLISHED"
            session.flush()
            published += 1
            log(f"{post.post_id}: PUBLISHED ({post.channel}, external={external_id})")
        except BrevoCreditsError as e:
            # Post stays RENDERED — retryable operator error, not a failure state.
            log(f"{post.post_id}: {e}")
        except (DoublePublishError, FirstPublishNotConfirmed):
            raise
        except Exception as e:
            post.status = "FAILED"
            post.park_reason = f"publish: {type(e).__name__}: {e}"[:500]
            session.flush()
            log(f"{post.post_id}: publish FAILED — {type(e).__name__}: {e}")
            # Surface the failing call site — a message alone gives nothing to locate a
            # bug by (the redaction filter still guards every line).
            log(traceback.format_exc())
    return {"published": published, "skipped": skipped}
