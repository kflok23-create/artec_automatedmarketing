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
from app.integrations.upload_post_client import extract_external_id
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


def publish(session: Session, drive, fal, uploader, brevo,
            post_ids: list[str] | None = None, all_rendered: bool = False,
            confirm: bool = True, log=print) -> dict:
    q = select(Post).where(Post.status == "RENDERED").order_by(Post.post_id)
    posts = [p for p in session.execute(q).scalars()
             if all_rendered or (post_ids and p.post_id in post_ids)]
    if not posts:
        log("publish: no RENDERED posts selected")
        return {"published": 0}

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
                    "first publish requires confirmation — re-run `hermes publish` and "
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
                # channel_media decides the media kind — the source of truth for surfaces.
                media_spec = get_config(session, "channel_media").get(post.channel, {})
                kind = media_spec.get("media", "photo")
                suffix = ".mp4" if kind == "video" else ".jpg"
                local = drive.download(post.media_drive_file_id, suffix=suffix)
                title = f"{post.caption}\n{post.tracked_url}"
                if kind == "video":
                    resp = uploader.upload_video(post.channel, local, title)
                else:
                    resp = uploader.upload_photo(post.channel, local, title)
                external_id = extract_external_id(resp)
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
    return {"published": published}
