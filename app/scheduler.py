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

# LEGACY BANNER ONLY — app/jobs.py:JOBS is the single source of truth for the twelve.
# Kept so the boot line still names something; the count comes from the registry.
JOBS = (
    {"name": "registry", "owner": "artec", "schedule": "see app/jobs.py"},
)


def next_runs(now: datetime | None = None) -> list[dict]:
    """What this scheduler will fire, and WHEN, with an explicit offset on every timestamp.

    `hermes cron create` has been observed rejecting `SUN` while exiting 0, which is why
    registration is only ever proven by LISTING. The artec side has no external registry to
    list, so it lists itself — and every next-run carries `+08:00` so a timezone regression
    is visible rather than inferred.
    """
    from app.jobs import firings

    now = now or datetime.now(SGT)
    out = []
    for job, when in firings():
        parts = when.split()
        dow, hhmm = (int(parts[0]), parts[1]) if len(parts) == 2 else (None, parts[0])
        hour, minute = (int(x) for x in hhmm.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if dow is not None:
            # 0 = Sunday, matching the numeric day-of-week cron requires.
            while (candidate.weekday() + 1) % 7 != dow:
                candidate += timedelta(days=1)
        out.append({"number": job.number, "name": job.name, "at": when,
                    "next_run": candidate.isoformat()})
    return sorted(out, key=lambda r: (r["number"], r["at"]))


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


def run_registry_job(session, job, log=print):
    """Dispatch one registry job. Explicit, not `importlib` on `job.body`: a typo in a
    dotted string would fail at 20:40 on a Tuesday instead of in CI."""
    from app.settings import get_settings

    settings = get_settings()
    if job.name == "report":
        from app.config import get_config
        from app.stages.report import build_report
        from app.toolbox.reconcile import reconcile_prices

        # FIRST action, before the snapshot.
        reconcile_prices(session, int(get_config(session, "render_run_cap_cents", 250)),
                         log=log)
        return build_report(session)
    if job.name == "bespoke-learn-ideate":
        from app.integrations.anthropic_client import LLM
        from app.stages.ideate import ideate
        from app.stages.learn import learn

        # learn THEN ideate, in that order, and both before job 3 at 07:00 — plan-diff at
        # 08:00 needs both sides or it agrees with itself.
        learn(session, LLM(settings), log=log)
        return ideate(session, LLM(settings), log=log)
    if job.name == "plan-diff":
        from app.stages.plan_diff import build_diff

        return build_diff(session, _last_monday())
    if job.name == "render":
        from app.integrations.anthropic_client import LLM
        from app.integrations.drive_client import DriveClient
        from app.integrations.fal_client import Fal
        from app.stages.render import render

        return render(session, LLM(settings), DriveClient(settings), Fal(settings),
                      all_approved=True, log=log)
    if job.name == "pg-dump":
        from app.integrations.drive_client import DriveClient
        from app.stages.backup import restore_check, rides_today, run_backup

        result = run_backup(settings.DATABASE_URL, drive=DriveClient(settings), log=log)
        if rides_today():
            proof = restore_check(session, settings.DATABASE_URL, result["local_path"],
                                  log=log)
            result["restore_check"] = {"ok": proof.ok, "detail": proof.detail}
        return result
    if job.name == "assets-sync":
        from app.integrations.drive_client import DriveClient
        from app.stages.assets_sync import sync
        from app.stages.wishlist import match

        out = sync(session, DriveClient(settings), log=log)
        out["wishlist"] = match(session, log=log)
        return out
    if job.name == "doctor-sweep":
        from app.config import set_config
        from app.stages.doctor import run_doctor

        checks = run_doctor(settings, session=session, log=log)
        set_config(session, "last_doctor", {
            "at": datetime.now(UTC).isoformat(),
            "checks": [{"name": c.name,
                        "status": "GREEN" if c.ok else ("YELLOW" if c.warn else "RED"),
                        "detail": c.detail} for c in checks]})
        return {"checks": len(checks)}
    if job.name == "digest-prepare":
        from app.integrations.brevo_client import Brevo
        from app.stages.digest import prepare_digest

        # The expiry sweep runs INSIDE job 11, immediately before preparation, so a review
        # that has just expired is parked and reported in the same payload.
        expired = sweep_expired_reviews(session, log=log)
        try:
            brevo = Brevo(settings)
        except Exception:
            brevo = None
        payload = prepare_digest(session, brevo=brevo, log=log)
        return {"expired": len(expired), "needs_you": not payload["needs_you"]["empty"]}
    raise RuntimeError(f"job {job.number} {job.name!r} has no dispatch")


def _last_monday():
    today = datetime.now(SGT).date()
    return today - timedelta(days=today.weekday())


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

    from app.jobs import firings

    with record_run("scheduler tick", {"at": f"{day} {hhmm}"}) as (session, rec):
        # THE REGISTRY IS WHAT FIRES. Until 2c-iv the registry knew the times and this loop
        # did not read it, so six jobs had times nothing acted on — a schedule that exists
        # only in a data structure.
        for job, when in firings():
            parts = when.split()
            dow, at = (int(parts[0]), parts[1]) if len(parts) == 2 else (None, parts[0])
            if hhmm != at:
                continue
            if dow is not None and (now.weekday() + 1) % 7 != dow:
                continue
            key = f"{day}|job{job.number}|{when}"
            if key in fired:
                continue
            fired.add(key)
            rec.log(f"scheduler: job {job.number} {job.name} due at {when}")
            try:
                run_registry_job(session, job, log=rec.log)
            except Exception as e:                              # noqa: BLE001
                # One job must not take the tick down with it — the rest of the night's
                # chain still has to run, and the digest is what reports the failure.
                rec.log(f"job {job.number} {job.name} FAILED: {type(e).__name__}: {e}")

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
    from app.jobs import artec_jobs

    print(f"artec-scheduler up — {len(artec_jobs())} registry jobs, timezone Asia/Singapore")
    for row in next_runs():
        # VERIFY BY LISTING, on this side too. The brain lists via `hermes cron list`; the
        # scheduler has no external registry, so it lists itself at every boot.
        print(f"  job {row['number']:>2} {row['name']:<22} next {row['next_run']}")
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
