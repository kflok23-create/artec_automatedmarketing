"""GATE — the one weekly human touchpoint, over Telegram inline buttons. Interactive,
blocking, human-driven; long polling inside the invoked command (no webhook, no scheduler).

- Approve / Edit / Reject buttons per DRAFT, one message per draft.
- Reject sets REJECTED and NEVER regenerates a replacement — fewer posts that week.
- Free text starting with '+' injects a brand-new idea as an APPROVED post.
- Edit accepts free-text 'field: value' lines overwriting named genome fields.
- --wishlist also lists PARKED posts and their wishlist entries in the same session.
- Ends on /done or timeout.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config, next_post_id
from app.models import Post
from app.spine import build_tracked_url, utm_dict

EDITABLE_FIELDS = ("angle", "hook", "cta_type", "cta_placement", "slot", "caption", "channel")


def _draft_summary(p: Post) -> str:
    return (
        f"<b>{p.post_id}</b> · {p.channel} · slot={p.slot or '-'}\n"
        f"angle: {p.angle or '-'}\n"
        f"hook: {p.hook or '-'}\n"
        f"cta: {p.cta_type or '-'} @ {p.cta_placement or '-'}\n"
        f"keywords: {', '.join(p.keywords or [])}"
    )


def _apply_edit(post: Post, text: str) -> list[str]:
    changed = []
    for line in text.splitlines():
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        if field in EDITABLE_FIELDS:
            setattr(post, field, value.strip())
            changed.append(field)
    return changed


def _inject_idea(session: Session, text: str) -> Post:
    """'+ channel: tiktok | hook: ...' or plain '+ my idea' (defaults to instagram)."""
    body = text.lstrip("+").strip()
    fields: dict[str, str] = {}
    if "|" in body or ":" in body:
        for part in body.split("|"):
            if ":" in part:
                k, _, v = part.partition(":")
                fields[k.strip().lower()] = v.strip()
    channel = fields.pop("channel", "instagram")
    medium = "email" if channel == "email" else "organic"
    post_id = next_post_id(session)
    week = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())
    site = get_config(session, "site_base_url")
    social = get_config(session, "social_code")
    email = get_config(session, "email_code")
    post = Post(
        post_id=post_id,
        week_start=week,
        channel=channel,
        status="APPROVED",
        hook=fields.get("hook") or (body if not fields else None),
        angle=fields.get("angle"),
        cta_type=fields.get("cta_type", "discount"),
        cta_placement=fields.get("cta_placement", "caption_end"),
        slot=fields.get("slot"),
        keywords=[],
        code=email if medium == "email" else social,
        utm=utm_dict(post_id, channel, medium),
        tracked_url=build_tracked_url(post_id, channel, medium, site_base_url=site,
                                      social_code=social, email_code=email),
    )
    session.add(post)
    session.flush()
    return post


def gate(session: Session, tg, timeout: int = 3600, wishlist: bool = False, log=print) -> dict:
    drafts = {p.post_id: p for p in session.execute(
        select(Post).where(Post.status == "DRAFT").order_by(Post.post_id)
    ).scalars()}

    if not drafts and not wishlist:
        log("gate: no DRAFT posts")
        return {"approved": 0, "rejected": 0, "edited": 0, "injected": 0}

    for p in drafts.values():
        tg.send_message(
            _draft_summary(p),
            buttons=[[
                {"text": "✅ Approve", "callback_data": f"app:{p.post_id}"},
                {"text": "✏️ Edit", "callback_data": f"edit:{p.post_id}"},
                {"text": "❌ Reject", "callback_data": f"rej:{p.post_id}"},
            ]],
        )
    if wishlist:
        parked = list(session.execute(select(Post).where(Post.status == "PARKED")).scalars())
        if parked:
            lines = ["<b>PARKED — awaiting assets</b>"]
            for p in parked:
                for w in p.asset_wishlist or []:
                    lines.append(f"{p.post_id} → {w.get('target_folder')}: {w.get('description')}")
            tg.send_message("\n".join(lines))
    tg.send_message("Reply /done to finish. '+ your idea' injects an approved post.")

    counts = {"approved": 0, "rejected": 0, "edited": 0, "injected": 0}
    pending_edit: str | None = None
    offset: int | None = None
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        updates = tg.get_updates(offset=offset, timeout_s=25)
        done = False
        for u in updates:
            offset = u["update_id"] + 1
            cb = u.get("callback_query")
            if cb:
                action, _, pid = (cb.get("data") or "").partition(":")
                post = drafts.get(pid) or session.get(Post, pid)
                if post is None:
                    continue
                if action == "app":
                    post.status = "APPROVED"
                    counts["approved"] += 1
                    tg.answer_callback(cb.get("id", ""), f"{pid} approved")
                elif action == "rej":
                    # NEVER regenerate a replacement — a rejected slot means fewer posts.
                    post.status = "REJECTED"
                    counts["rejected"] += 1
                    tg.answer_callback(cb.get("id", ""), f"{pid} rejected")
                elif action == "edit":
                    pending_edit = pid
                    tg.answer_callback(cb.get("id", ""), "")
                    tg.send_message(f"Editing {pid} — send 'field: value' lines ({', '.join(EDITABLE_FIELDS)})")
                session.flush()
                continue
            msg = (u.get("message") or {}).get("text", "")
            if not msg:
                continue
            if msg.strip() == "/done":
                done = True
                break
            if msg.strip().startswith("+"):
                post = _inject_idea(session, msg)
                counts["injected"] += 1
                tg.send_message(f"Injected {post.post_id} ({post.channel}) as APPROVED")
                continue
            if pending_edit:
                post = drafts.get(pending_edit) or session.get(Post, pending_edit)
                if post is not None:
                    changed = _apply_edit(post, msg)
                    session.flush()
                    tg.send_message(f"{pending_edit}: updated {', '.join(changed) or 'nothing'}")
                pending_edit = None
        if done:
            break

    session.flush()
    log(f"gate: {counts}")
    return counts
