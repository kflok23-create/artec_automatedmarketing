"""artec-scheduler — the daily body. EXACTLY TWO jobs live here (2 of the 4 v3 scheduled
jobs; the other two are hermes-agent cron on hermes-brain):

  daily-publish-by-slot  every APPROVED+RENDERED post whose slot time has arrived
                         (slot is a real Asia/Singapore firing time AND a learned lever)
  daily-measure-0630     the 06:30 measure prompt — lists unmeasured PUBLISHED posts to
                         Telegram; figures still enter via `artec measure` / POST
                         /commands/measure (no channel APIs, no CSVs)

This is the narrow, deliberate lift of v2's blanket no-scheduler rule (§9). Nothing else
in either codebase fires on a clock — a repo test counts the jobs.

Run as its own Railway service: `python -m app.scheduler`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.settings import get_settings, install_redaction

SGT = ZoneInfo("Asia/Singapore")

JOBS = (
    {"name": "daily-publish-by-slot", "owner": "artec", "schedule": "daily at each config slot_times entry"},
    {"name": "daily-measure-0630", "owner": "artec", "schedule": "daily 06:30 Asia/Singapore"},
)


def select_due_posts(session, slot: str) -> list:
    """RENDERED or APPROVED_TO_SEND posts for this slot that have NEVER been published.

    APPROVED_TO_SEND is included HERE and nowhere earlier: an approval at 21:15 does not
    send at 21:15, it waits for the next occurrence of the post's plan-assigned slot.
    Send time stays a learned lever, and 21:15 is a poor one.

    The external_post_id filter is the never-republish guard at the selection layer
    (publish() enforces it again — belt and braces; v3 acceptance 21).
    """
    from app.models import Post
    from app.stages.publish import PUBLISHABLE_STATUSES

    return list(session.execute(
        select(Post)
        .where(Post.status.in_(PUBLISHABLE_STATUSES))
        .where(Post.slot == slot)
        .where(Post.external_post_id.is_(None))
        .order_by(Post.post_id)
    ).scalars())


def sweep_orphaned_slots(session) -> list[dict]:
    """v4 §7·A7, second guard: RENDERED posts whose slot matches no key of `slot_times`
    will never be selected by any slot pass — they would sit forever, silently. Both
    guards exist because write-time validation cannot fix rows written before it existed,
    or rows orphaned by an operator editing slot_times.

    Returns them for the digest. Reporting, not deleting — the operator decides.
    """
    from app.config import get_config
    from app.models import Post

    slot_times = get_config(session, "slot_times", {}) or {}
    orphans = session.execute(
        select(Post).where(Post.status == "RENDERED", Post.external_post_id.is_(None))
    ).scalars()
    return [
        {"post_id": p.post_id, "channel": p.channel, "slot": p.slot,
         "reason": f"slot {p.slot!r} is not in slot_times {sorted(slot_times)}"}
        for p in orphans
        if p.slot not in slot_times
    ]


def sweep_expired_reviews(session, now: datetime | None = None, log=print) -> list[dict]:
    """v4 §E — a review that is never answered PARKS. There is no auto-approve and no
    expire-to-send under any framing: not "the operator was slow", not "the copy is
    unchanged from last week", not "pre-flight already passed". Stale email is worse than
    no email, and an unwatched video is an unwatched video however long it has waited.

    The window is per-surface config (`email_review_expiry_days` / `video_review_expiry_days`)
    and is measured from delivery/presentation, not from render.
    """
    from app.config import get_config
    from app.models import Post

    now = now or datetime.now(UTC)
    windows = {"email": int(get_config(session, "email_review_expiry_days", 3)),
               "video": int(get_config(session, "video_review_expiry_days", 3))}
    expired = []
    for post in session.execute(
        select(Post).where(Post.status.in_(("RENDERED", "APPROVED_TO_SEND")))
        .order_by(Post.post_id)
    ).scalars():
        from app.stages.publish import carries_video

        surface = "email" if post.channel == "email" else (
            "video" if carries_video(session, post) else None)
        if surface is None:
            continue
        review = (post.email_review if surface == "email" else post.video_review) or {}
        if review.get("decision"):
            continue                       # answered — expiry does not apply
        started = review.get("delivered_at") or review.get("presented_at")
        if not started:
            continue                       # never presented → nothing has expired yet
        started_at = datetime.fromisoformat(str(started))
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        age_days = (now - started_at).days
        if age_days < windows[surface]:
            continue
        post.status = "PARKED"
        post.park_reason = (f"{surface} review expired after {age_days}d without a decision "
                            "— parked, never sent")[:500]
        review["expired_at"] = now.isoformat()
        if surface == "email":
            post.email_review = dict(review)
        else:
            post.video_review = dict(review)
        session.flush()
        expired.append({"post_id": post.post_id, "surface": surface, "age_days": age_days})
        log(f"{post.post_id}: {surface} review expired after {age_days}d — PARKED")
    return expired


def run_publish_job(session, slot: str, log=print) -> dict:
    from app.config import get_config
    from app.integrations.brevo_client import Brevo
    from app.integrations.drive_client import DriveClient
    from app.integrations.fal_client import Fal
    from app.integrations.upload_post_client import UploadPost
    from app.stages.publish import publish

    due = select_due_posts(session, slot)
    if not due:
        return {"published": 0}
    if bool(get_config(session, "confirm_first_publish", True)):
        log("scheduler: first publish has not been confirmed via the CLI yet — skipping "
            "(run `artec publish` once by hand)")
        return {"published": 0, "skipped": "first-publish gate"}
    settings = get_settings()
    return publish(session, DriveClient(settings), Fal(settings), UploadPost(settings),
                   Brevo(settings), post_ids=[p.post_id for p in due], all_rendered=False,
                   confirm=False, log=log)


def run_measure_job(session, log=print) -> dict:
    """06:30 — the measure prompt: unmeasured PUBLISHED posts go to Telegram so the
    operator can reply with figures via `artec measure`."""
    from app.integrations.telegram_client import Telegram
    from app.stages.measure import unmeasured_posts

    target = (datetime.now(SGT) - timedelta(days=1)).date()
    pending = unmeasured_posts(session, target)
    if not pending:
        log(f"measure {target}: nothing unmeasured")
        return {"pending": 0}
    lines = [f"MEASURE {target} — {len(pending)} post(s) unmeasured (blank stays NULL, never 0):"]
    for p in pending:
        lines.append(f"  {p.post_id} · {p.channel} · {(p.hook or '')[:60]}")
    lines.append("Enter figures: artec measure  (or POST /commands/measure)")
    try:
        Telegram(get_settings()).send_message("\n".join(lines))
    except Exception as e:
        log(f"measure reminder: telegram send failed ({type(e).__name__})")
    log(f"measure {target}: {len(pending)} unmeasured post(s), reminder sent")
    return {"pending": len(pending)}


def tick(now: datetime, fired: set[str], log=print) -> set[str]:
    """One scheduler heartbeat. `fired` carries the day's already-fired keys so a job
    fires once per day per slot. Pure enough to test."""
    from app.config import get_config
    from app.db import record_run

    hhmm = now.strftime("%H:%M")
    day = now.strftime("%Y-%m-%d")

    with record_run("scheduler tick", {"at": f"{day} {hhmm}"}) as (session, rec):
        slot_times: dict = get_config(session, "slot_times")
        for slot, at in slot_times.items():
            key = f"{day}|publish|{slot}"
            if hhmm == at and key not in fired:
                fired.add(key)
                rec.log(f"scheduler: slot '{slot}' arrived — publishing due posts")
                run_publish_job(session, slot, log=rec.log)
        measure_at = get_config(session, "measure_reminder_time", "06:30")
        key = f"{day}|measure"
        if hhmm == measure_at and key not in fired:
            fired.add(key)
            run_measure_job(session, log=rec.log)
    return fired


def main() -> None:
    settings = get_settings()
    install_redaction(settings)
    print(f"artec-scheduler up — {len(JOBS)} jobs, timezone Asia/Singapore")
    fired: set[str] = set()
    current_day = datetime.now(SGT).strftime("%Y-%m-%d")
    while True:
        now = datetime.now(SGT)
        if now.strftime("%Y-%m-%d") != current_day:
            current_day = now.strftime("%Y-%m-%d")
            fired = set()
        try:
            fired = tick(now, fired)
        except Exception as e:
            print(f"scheduler tick error: {type(e).__name__}: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
