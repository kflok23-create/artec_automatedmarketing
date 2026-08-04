"""v4 seam additions — nine new tools (six + nine = fifteen).

Same contract as tools.py: `(args: dict, **kwargs) -> str`, JSON envelope always, never
raises. Self-contained — sqlalchemy textual SQL plus httpx for the two tools that must
reach Telegram/Brevo. No tool writes `orders`, `events`, or `config`; `metrics` becomes
writable by TRANSCRIPTION ONLY (see record_metrics + the hook in tools.py).

Tool count note: the v4 prompt specified fourteen and declared `read_digest` READ ONLY.
Delivering a video natively AND recording the delivery receipt cannot both live in a
read-only tool, so the operator elected the fifteenth tool — `deliver_video` — rather than
weakening read_digest. That is the cleaner shape: read stays read, delivery is explicit.

Video delivery design (CORRECTED): `deliver_video` uploads THE PUBLISH BYTES multipart.

The first build handed Telegram the fal URL. That defeated both halves of the design: the
operator approved fal's copy while publish streams the Drive copy (§7.9 stores every render
in `_generated/{week}/{post_id}.{ext}`, and §4 makes the fal URL a fallback only), and
Telegram's rejection of a malformed file — the independent validity check the whole gate
rests on — was validating somebody else's bytes.

The brain has no Drive credentials and must not grow any, so it reads the bytes from the
app's authenticated `GET /commands/media/{post_id}`, which resolves them through
`publish_media_path` — the same function publish calls. Byte-identity is proven by sha256,
not assumed. A missing Drive file PARKS; there is no fal fallback, because a silent
fallback is precisely the divergence being closed.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from .tools import _config, _get_engine, _jtext, _log_run

EDITABLE_EMAIL_VARS = ("subject", "headline", "body_copy", "cta_text", "story_block",
                       "hero_image_url", "tracked_url")
METRIC_FIELDS = ("impressions", "completion_rate", "watch_time_s", "saves", "shares", "clicks")

try:                                            # stdlib since 3.9; guarded for odd images
    from zoneinfo import ZoneInfo

    SGT = ZoneInfo("Asia/Singapore")
except Exception:                               # pragma: no cover
    SGT = UTC


# ---------------------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------------------

GATE_ACTIONS = {
    "approve": 'say  approve <post_id>            — e.g. "approve post_1497"',
    "edit": 'say  edit <post_id> <field>=<value>  — e.g. "edit post_1497 hook=time"',
    "reject": 'say  reject <post_id> <reason>      — e.g. "reject post_1497 stale week"',
    "inject": 'say  inject <channel> <slot>        — e.g. "inject facebook lunch"',
}

# The fields `edit` may overwrite. Named so the agent states them BEFORE applying, rather
# than the operator discovering afterwards which parts of the genome moved.
EDITABLE_GENOME = ("angle", "hook", "cta_type", "cta_placement", "slot", "caption",
                   "keywords")


def gate_vocabulary() -> list[str]:
    """The exact wording that invokes each action, rendered inline with the drafts.

    D-5. The operator typed `Job 11 approved` at the first live digest — a reasonable thing
    to type and not a defined action. Nothing in the message had told them what was. Sunday
    is 45 minutes of consequential decisions in free text and it happens BEFORE any digest
    work, so this is the larger of the two surfaces and it comes first.

    THE AGENT DOES NOT INFER OPERATOR INTENT. That is the transcriber invariant generalised:
    it may not guess what `Job 11 approved` meant any more than it may originate a figure.
    An unrecognised reply asks, naming the valid actions for what is actually pending.
    """
    lines = ["ACTIONS — use these exact words:"]
    lines += [f"  {v}" for v in GATE_ACTIONS.values()]
    lines.append(f"  edit overwrites ONLY: {', '.join(EDITABLE_GENOME)} — it will say which "
                 "field it is changing, and to what, before applying it")
    lines.append("  anything else: I will ask rather than guess. I do not infer what an "
                 "instruction meant.")
    return lines


def _gate_opening_summary(week_start: str, drafts: list[dict],
                          today=None) -> list[str]:
    """WHICH WEEK IS BEING GATED, ON THE SCREEN, NEVER INFERRED.

    D-vii. `next_week_start` returns the Monday that already passed when called on a Sunday,
    so 2026-08-09's gate reads week 2026-08-03 — the week that just ended, which already
    holds nine DRAFTs and five PUBLISHED posts. The operator decided to leave that arithmetic
    alone through Sunday rather than change a date function 72 hours before it first fires.

    A deferral becomes a decision only if the thing deferred is visible. So the gate opens by
    stating the week it is gating, how many drafts it found, and how old the oldest is. If
    the week is wrong, it is wrong ON THE SCREEN at 09:00 — not discovered afterwards from
    a plan that quietly never happened.
    """
    from datetime import date, datetime

    today = today or datetime.now(SGT).date()
    lines = [f"GATE — week_start {week_start}", f"  {len(drafts)} draft(s) found"]
    if not drafts:
        lines.append("  NO DRAFTS FOR THIS WEEK. That is either a week nobody planned or "
                     "the wrong week — check the date above before concluding either.")
        return lines
    try:
        started = date.fromisoformat(str(week_start))
        day = (today - started).days
        lines.append(f"  day {day} of that week (it runs {started} to "
                     f"{started.fromordinal(started.toordinal() + 6)})")
        # THE THRESHOLD IS 6, NOT 7, AND THE FIRST VERSION OF THIS GOT IT WRONG.
        # A gate is meant to plan the week AHEAD. Week 2026-08-03 runs Mon 03 → Sun 09, so
        # on Sunday 2026-08-09 it is day 6 — the week is ENDING, not ended. A `>= 7` test
        # would never have fired on the one day this check exists for, which is a guard
        # that cannot fail on the case it was written for.
        if day >= 6:
            lines.append(
                f"  *** THIS WEEK IS ALREADY OVER OR ENDING TODAY (day {day} of 7). These "
                "drafts were planned for it, so approving them publishes into a week with "
                "almost no slots left — the remaining ones spill into next week or never "
                "fire at all. See DECISIONS.md 107 (D-vii): next_week_start returns the "
                "Monday that already passed when called on a Sunday.")
    except ValueError:
        lines.append(f"  could not parse week_start {week_start!r}")
    off = [d for d in drafts if d.get("slot_off_vocabulary")]
    if off:
        lines.append(f"  *** {len(off)} draft(s) carry a slot that is NOT in slot_times: "
                     f"{[d['post_id'] for d in off]}. A post with an off-vocabulary slot "
                     "renders, then is never selected by any slot pass, and never errors. "
                     "Approving one spends render money on a post that cannot publish.")
    return lines


def _read_draft_posts_impl(week_start: str, engine=None) -> list[dict]:
    """Every DRAFT for the week with its full creative genome — closes gap A1.

    The gate previously had to fish drafts out of v_brief's LIMIT 14 window, which was
    undesigned and breaks as cadence rises. This is the designed path.

    Each draft carries `slot_off_vocabulary` and the action vocabulary travels with the
    result, so the gate cannot present a draft without also presenting how to act on it.
    """
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_draft_posts", {"week_start": week_start})
        rows = conn.execute(text(
            "SELECT post_id, channel, status, angle, hook, cta_type, cta_placement, "
            "keywords, slot, plan_source, tracked_url FROM posts "
            "WHERE week_start = :w AND status = 'DRAFT' ORDER BY channel, slot, post_id"),
            {"w": week_start}).all()
        slot_times = _config(conn, "slot_times", {}) or {}
    out = []
    for r in rows:
        keywords = r[7]
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except (TypeError, json.JSONDecodeError):
                keywords = []
        out.append({"post_id": r[0], "channel": r[1], "status": r[2], "angle": r[3],
                    "hook": r[4], "cta_type": r[5], "cta_placement": r[6],
                    "keywords": keywords or [], "slot": r[8], "plan_source": r[9],
                    "tracked_url": r[10],
                    # A7 AT THE GATE, NOT AFTER THE RENDER. `sweep_orphaned_slots` catches
                    # an off-vocabulary slot only once the post is RENDERED — which is
                    # after the fal spend. Surfacing it here makes it impossible to approve
                    # one without having been told.
                    "slot_off_vocabulary": r[8] not in slot_times,
                    "actions": list(GATE_ACTIONS)})
    return {"week_start": week_start, "drafts": out,
            "summary": _gate_opening_summary(week_start, out),
            "vocabulary": gate_vocabulary()}


def is_sunday(now: datetime | None = None) -> bool:
    """Asia/Singapore, because the whole schedule is in SGT."""
    return (now or datetime.now(SGT)).weekday() == 6


def _read_digest_impl(digest_date: str | None = None, now: datetime | None = None,
                      engine=None) -> dict:
    """The digest payload prepared by job 11, plus the pre-split Telegram messages to send
    verbatim in order. READ ONLY — delivery of video is deliver_video's job.

    JOB 12 DOES NOT RUN ON SUNDAY: the 09:00 gate is that day's touch, and a second
    Telegram session the same evening spends the operator's attention twice. The cron
    expression says so too, but a cron expression is one edit away from being wrong — so
    the refusal lives here, in the body, where nothing can route around it.
    """
    eng = _get_engine(engine)
    # THE ONE SHARED FUNCTION. This read `date.today()` — the container's local date, which
    # is UTC — while job 11 wrote `now(UTC).date() - 1 day`. Two correct implementations,
    # two conventions, and on 2026-08-04 the first digest this system ever produced was
    # written under 2026-08-03 and looked for under 2026-08-04.
    from app.digest_dates import digest_date_for

    target = str(digest_date or digest_date_for(now))
    if is_sunday(now):
        return {"date": target, "deliver": False,
                "skip_reason": "Sunday — job 12 does not run; the 09:00 gate is today's "
                               "human touch and the digest resumes Monday 21:00"}
    with eng.begin() as conn:
        _log_run(conn, "read_digest", {"date": target})
        row = conn.execute(text(
            "SELECT payload, delivered_at FROM digests WHERE digest_date = :d"),
            {"d": target}).first()
        if row is None:
            # THREE STATES, NEVER COLLAPSED INTO ONE. On 2026-08-04 this returned "job 11
            # has not run" while a digest carrying ACTION REQUIRED content sat in the table
            # under the previous day's key. Job 11 HAD run, on time, unattended, for the
            # first time — and the message told the operator it had not. A mismatch and an
            # absence are different faults and only one of them is the scheduler's.
            recent = conn.execute(text(
                "SELECT digest_date, delivered_at FROM digests "
                "ORDER BY digest_date DESC LIMIT 1")).first()
            if recent is not None:
                other, other_delivered = recent
                return {
                    "date": target, "prepared": False, "deliver": False,
                    "fault": "digest_date_mismatch",
                    "note": (
                        f"FAULT: no digest for {target}, but the most recent digest is "
                        f"dated {other} (delivered_at={other_delivered}). Job 11 ran and "
                        f"wrote a row under a DIFFERENT key — this is a date-convention "
                        f"fault, not a missing run. Both dates are named deliberately: "
                        f"reporting this as an absent run sends the operator to debug the "
                        f"scheduler when the scheduler is working."
                    ),
                    "expected_date": target, "found_date": str(other),
                }
            return {"date": target, "prepared": False, "deliver": False,
                    "note": "no digest prepared for this date, and no digest exists at all "
                            "— job 11 has not run"}
        payload, delivered_at = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        # Mark delivered on first read by the delivery job; idempotent thereafter.
        if delivered_at is None:
            conn.execute(text(
                "UPDATE digests SET delivered_at = :t WHERE digest_date = :d"),
                {"t": datetime.now(UTC), "d": target})
    return {"date": target, "prepared": True, "deliver": True, **(payload or {})}


# ---------------------------------------------------------------------------------------
# video review — deliver, then decide. Never the reverse.
# ---------------------------------------------------------------------------------------

class MediaUnavailable(RuntimeError):
    """The publish bytes could not be fetched. Distinguishes 'the file is not in Drive'
    (park) from 'the API is unreachable' (retry)."""

    def __init__(self, message: str, park: bool):
        super().__init__(message)
        self.park = park


def _publish_bytes(post_id: str) -> tuple[bytes, str, str]:
    """THE bytes publish() will stream, read through the app's authenticated media
    endpoint — which resolves them with `publish_media_path`, the same function publish
    uses. Returns (data, filename, sha256).

    The brain holds no Drive credentials and must not grow any; this is the whole reason
    the endpoint exists. There is NO fal-URL fallback: handing Telegram a remote URL makes
    Telegram validate fal's copy of the file, which says nothing about the bytes that reach
    Upload-Post, and lets the operator approve one artefact while another goes live.
    """
    import hashlib

    base = (os.environ.get("ARTEC_API_BASE") or "").rstrip("/")
    token = os.environ.get("HERMES_API_TOKEN", "")
    if not base or not token:
        raise MediaUnavailable(
            "ARTEC_API_BASE / HERMES_API_TOKEN are not set on this service — the video "
            "cannot be read from Drive, and a fal URL is not an acceptable substitute",
            park=False)
    url = f"{base}/commands/media/{post_id}"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    except Exception as e:
        # NAME THE URL. ARTEC_API_BASE carries a hardcoded :8080 while Railway injects
        # PORT, so a port change must surface as "cannot reach artec api at <url>" rather
        # than as a mute deliver_video refusal that looks like a bad file.
        raise MediaUnavailable(
            f"cannot reach artec api at {url} — {type(e).__name__}: {e}. Check "
            "ARTEC_API_BASE (the port is pinned there; Railway injects PORT).",
            park=False) from None
    if resp.status_code == 404:
        raise MediaUnavailable(
            f"the rendered file is not in Drive _generated/ ({resp.text[:200]})", park=True)
    if resp.status_code >= 400:
        raise MediaUnavailable(
            f"artec api at {url} returned HTTP {resp.status_code}"
            + (" — the bearer was rejected; HERMES_API_TOKEN differs between services"
               if resp.status_code == 401 else ""), park=False)
    data = resp.content
    claimed = resp.headers.get("X-Artec-Media-SHA256", "")
    actual = hashlib.sha256(data).hexdigest()
    if claimed and claimed != actual:
        raise MediaUnavailable(
            f"media digest mismatch: endpoint said {claimed[:12]}…, bytes hash "
            f"{actual[:12]}… — refusing to show the operator a file we cannot identify",
            park=False)
    return data, resp.headers.get("X-Artec-Media-Filename", f"{post_id}.mp4"), actual


def _deliver_video_impl(post_id: str, engine=None) -> dict:
    """Send the rendered video into the chat as a NATIVE Telegram video message — the
    ACTUAL BYTES, uploaded multipart — and record the delivery message_id. `review_video`
    refuses without that receipt, so nobody can approve a video they were never shown.

    Telegram refusing the upload PARKs the post — that refusal is independent evidence the
    file is malformed and is more trustworthy than our own ffprobe pass. That evidence is
    only worth having because the bytes are ours: a URL would have Telegram validating
    somebody else's copy.
    """
    eng = _get_engine(engine)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return {"post_id": post_id, "error": "telegram credentials absent in this process"}

    with eng.begin() as conn:
        _log_run(conn, "deliver_video", {"post_id": post_id})
        row = conn.execute(text(
            "SELECT status, caption, slot, channel, video_review FROM posts "
            "WHERE post_id = :p"), {"p": post_id}).first()
        if row is None:
            return {"post_id": post_id, "error": "not found"}
        status, caption, slot, channel, review = row
        # SQLite round-trips a NULL json column as the string "null", which json.loads turns
        # back into None — so the `or {}` must apply AFTER parsing, not instead of it.
        review = (json.loads(review) if isinstance(review, str) else review) or {}
        if review.get("telegram_message_id"):
            return {"post_id": post_id, "already_delivered": True,
                    "telegram_message_id": review["telegram_message_id"]}

    now = datetime.now(UTC)
    try:
        data, filename, digest = _publish_bytes(post_id)
    except MediaUnavailable as e:
        if not e.park:
            return {"post_id": post_id, "error": str(e), "parked": False}
        with eng.begin() as conn:
            review.update({"decision": None, "reason": str(e), "parked_at": now.isoformat()})
            conn.execute(_jtext(
                "UPDATE posts SET status = 'PARKED', park_reason = :r, video_review = :v "
                "WHERE post_id = :p", "v"),
                {"r": str(e)[:500], "v": review, "p": post_id})
        return {"post_id": post_id, "parked": True, "error": str(e)}

    cap = f"{post_id} · {channel} · slot {slot}\n{(caption or '')[:900]}"
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            # httpx requires the form fields as a DICT alongside files=; a list of tuples
            # mis-encodes (the Upload-Post multipart bug, in production, once).
            data={"chat_id": chat_id, "caption": cap, "supports_streaming": "true"},
            files={"video": (filename, data, "video/mp4")},
            timeout=180,
        )
        body = resp.json()
    except Exception as e:  # network/timeout — do NOT park on a transient error
        return {"post_id": post_id, "error": f"telegram send failed: {type(e).__name__}: {e}",
                "parked": False}

    if not body.get("ok"):
        # Telegram rejected the media itself → park. Independent evidence of a bad file.
        reason = f"telegram refused the video: {body.get('description', 'unknown')}"
        with eng.begin() as conn:
            review.update({"decision": None, "reason": reason, "parked_at": now.isoformat()})
            conn.execute(_jtext(
                "UPDATE posts SET status = 'PARKED', park_reason = :r, video_review = :v "
                "WHERE post_id = :p", "v"),
                {"r": reason[:500], "v": review, "p": post_id})
        return {"post_id": post_id, "parked": True, "error": reason}

    message_id = body["result"]["message_id"]
    expiry_days = 3
    with eng.begin() as conn:
        expiry_days = int(_config(conn, "video_review_expiry_days", 3))
        review.update({"telegram_message_id": message_id,
                       "delivered_at": now.isoformat(),
                       "expiry": (now + timedelta(days=expiry_days)).isoformat()})
        conn.execute(_jtext("UPDATE posts SET video_review = :v WHERE post_id = :p", "v"),
                     {"v": review, "p": post_id})
    return {"post_id": post_id, "telegram_message_id": message_id,
            "expires_in_days": expiry_days, "sha256": digest, "bytes": len(data)}


def _review_video_impl(post_id: str, decision: str, reason: str | None = None,
                       engine=None) -> dict:
    """approve → APPROVED_TO_SEND (publishes at the NEXT occurrence of its slot).
    reject → PARKED. rerender → back to APPROVED with the reason passed to the toolbox.

    Refused unless a delivery message_id exists: no configuration value, flag, or framing
    can approve a video the operator was never shown.
    """
    if decision not in ("approve", "reject", "rerender"):
        raise ValueError(f"unknown video decision {decision!r}")
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "review_video", {"post_id": post_id, "decision": decision,
                                        "reason": reason})
        row = conn.execute(text(
            "SELECT status, video_review FROM posts WHERE post_id = :p"), {"p": post_id}).first()
        if row is None:
            return {"post_id": post_id, "error": "not found"}
        status, review = row
        # SQLite round-trips a NULL json column as the string "null", which json.loads turns
        # back into None — so the `or {}` must apply AFTER parsing, not instead of it.
        review = (json.loads(review) if isinstance(review, str) else review) or {}
        if not review.get("telegram_message_id"):
            # Hard refusal, not an informational result: this is the guard that makes
            # "nobody approves a video they were never shown" true. It must reach the agent
            # as an unambiguous failure (ok:false), never as a success carrying a note.
            raise PermissionError(
                "refused: no Telegram delivery recorded for this video — call deliver_video "
                "first so the operator can actually watch it before deciding"
            )
        if review.get("decision"):
            return {"post_id": post_id, "already": review["decision"]}

        now = datetime.now(UTC)
        review.update({"decision": decision, "reason": reason,
                       "reviewed_at": now.isoformat()})
        if decision == "approve":
            new_status = "APPROVED_TO_SEND"
        elif decision == "reject":
            new_status = "PARKED"
        else:
            new_status = "APPROVED"  # re-enters the next render pass
            review["rerender_guidance"] = reason
        params = {"s": new_status, "v": review, "p": post_id}
        sql = "UPDATE posts SET status = :s, video_review = :v WHERE post_id = :p"
        if decision == "reject":
            sql = ("UPDATE posts SET status = :s, video_review = :v, park_reason = :r "
                   "WHERE post_id = :p")
            params["r"] = (reason or "rejected at video review")[:500]
        conn.execute(_jtext(sql, "v"), params)
    return {"post_id": post_id, "decision": decision, "status": new_status}


# ---------------------------------------------------------------------------------------
# email review — the only pre-publish approval gate, on the only irreversible surface
# ---------------------------------------------------------------------------------------

def _review_email_impl(post_id: str, decision: str, edits: dict | None = None,
                       send_test: bool = False, engine=None) -> dict:
    """approve → APPROVED_TO_SEND (sends at the NEXT slot occurrence, never immediately —
    21:15 is a poor send time and send-time stays a learned lever).
    edit → applies overwrites to any of the seven Brevo variables and re-renders.
    reject → PARKED with a reason.
    send_test → a Brevo test send to the operator's own address; changes no status.
    """
    if decision not in ("approve", "reject", "edit", "test_only"):
        raise ValueError(f"unknown email decision {decision!r}")
    eng = _get_engine(engine)
    now = datetime.now(UTC)
    with eng.begin() as conn:
        _log_run(conn, "review_email", {"post_id": post_id, "decision": decision,
                                        "edits": sorted(edits or {}), "send_test": send_test})
        row = conn.execute(text(
            "SELECT status, channel, caption, email_review FROM posts WHERE post_id = :p"),
            {"p": post_id}).first()
        if row is None:
            return {"post_id": post_id, "error": "not found"}
        status, channel, caption, review = row
        if channel != "email":
            return {"post_id": post_id, "error": f"not an email post (channel={channel})"}
        # SQLite round-trips a NULL json column as the string "null", which json.loads turns
        # back into None — so the `or {}` must apply AFTER parsing, not instead of it.
        review = (json.loads(review) if isinstance(review, str) else review) or {}
        if review.get("decision") and decision != "test_only":
            return {"post_id": post_id, "already": review["decision"]}

        copy = json.loads(caption) if isinstance(caption, str) and caption else {}

        if send_test or decision == "test_only":
            review.setdefault("test_sends", []).append(now.isoformat())
            conn.execute(_jtext("UPDATE posts SET email_review = :e WHERE post_id = :p", "e"),
                         {"e": review, "p": post_id})
            if decision == "test_only":
                return {"post_id": post_id, "test_send_requested": True,
                        "status_unchanged": status}

        if decision == "edit":
            bad = sorted(set(edits or {}) - set(EDITABLE_EMAIL_VARS))
            if bad:
                return {"post_id": post_id, "error": f"not editable: {bad}"}
            copy.update(edits or {})
            review.update({"decision": "edit", "edits": edits or {},
                           "reviewed_at": now.isoformat()})
            # back to RENDERED so the render pass re-materialises it; re-presented next digest
            conn.execute(_jtext(
                "UPDATE posts SET status = 'RENDERED', caption = :c, email_review = :e "
                "WHERE post_id = :p", "e"),
                {"c": json.dumps(copy, ensure_ascii=False), "e": review, "p": post_id})
            return {"post_id": post_id, "decision": "edit", "status": "RENDERED",
                    "re_presented": "next digest"}

        review.update({"decision": decision, "reviewed_at": now.isoformat()})
        if decision == "approve":
            new_status = "APPROVED_TO_SEND"
            conn.execute(_jtext(
                "UPDATE posts SET status = :s, email_review = :e WHERE post_id = :p", "e"),
                {"s": new_status, "e": review, "p": post_id})
        else:
            new_status = "PARKED"
            review["reason"] = (edits or {}).get("reason") if isinstance(edits, dict) else None
            conn.execute(_jtext(
                "UPDATE posts SET status = :s, email_review = :e, park_reason = :r "
                "WHERE post_id = :p", "e"),
                {"s": new_status, "e": review,
                 "r": (review.get("reason") or "rejected at email review")[:500],
                 "p": post_id})
    return {"post_id": post_id, "decision": decision, "status": new_status}


# ---------------------------------------------------------------------------------------
# metrics — TRANSCRIPTION ONLY. The agent may not compute, estimate, infer, or carry forward.
# ---------------------------------------------------------------------------------------

_DIGITS = re.compile(r"\d+")


def digits_in(value: Any) -> list[str]:
    """Digit runs in a value, separators stripped: '1,200' → ['1200']."""
    text_value = str(value).replace(",", "").replace(" ", "").replace("_", "")
    return _DIGITS.findall(text_value)


def transcription_violations(figures: dict, operator_message: str) -> list[str]:
    """Every digit run the agent submitted must appear in the operator's verbatim message.
    This is what makes the agent a transcriber rather than an originator."""
    haystack = str(operator_message).replace(",", "").replace(" ", "").replace("_", "")
    bad = []
    for field, value in (figures or {}).items():
        if value is None:
            continue
        for run in digits_in(value):
            if run not in haystack:
                bad.append(f"{field}={value}")
                break
    return bad


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class MetricsLineError(ValueError):
    """The ordered line does not parse. Refusing is correct: a mis-aligned line writes the
    right digits into the wrong columns, which is worse than no reading at all."""


def parse_metrics_line(line: str) -> dict:
    """One ordered reply — '4200, 0.62, 12, 45, 8, 118' — in the fixed field order.

    An EMPTY position is NULL, never zero: '4200, , , 45, , 118' records impressions,
    saves and clicks and leaves completion_rate, watch_time_s and shares unmeasured. A
    literal 0 is a measured zero and is kept.

    Thousands separators are refused rather than guessed at: '4,200' would silently become
    two positions and shift every later figure into the wrong field.
    """
    tokens = [t.strip() for t in str(line).split(",")]
    if len(tokens) > len(METRIC_FIELDS):
        raise MetricsLineError(
            f"{len(tokens)} positions for {len(METRIC_FIELDS)} fields "
            f"({', '.join(METRIC_FIELDS)}) — if you used a thousands separator, drop it "
            "(4200, not 4,200) and send the line again"
        )
    figures: dict = {}
    for index, (field, token) in enumerate(zip(METRIC_FIELDS, tokens, strict=False), start=1):
        if token == "":
            figures[field] = None            # unmeasured, never zero
            continue
        if not _NUMBER.fullmatch(token):
            raise MetricsLineError(
                f"position {index} ({field}) is not a number: {token!r} — send the figure "
                "or leave the position empty to record it as unmeasured"
            )
        figures[field] = float(token) if "." in token else int(token)
    return figures


def figures_from_args(args: dict) -> dict:
    """The figures a record_metrics call would write, from either input form. Used by the
    transcription hook so an ordered line is policed exactly like an explicit dict."""
    figures = {k: v for k, v in (args.get("figures") or {}).items() if k in METRIC_FIELDS}
    line = args.get("figures_line")
    if line:
        try:
            parsed = parse_metrics_line(line)
        except MetricsLineError:
            return figures                   # the tool refuses with the reason
        parsed.update(figures)
        return parsed
    return figures


def _echo(post_id: str, channel: str, metric_date: str, recorded: dict,
          null_fields: list[str]) -> str:
    body = ", ".join(f"{k}={v}" for k, v in recorded.items()) or "nothing"
    tail = (f"; {', '.join(null_fields)} stay UNMEASURED (NULL, not zero)"
            if null_fields else "")
    return f"{post_id} · {channel} · {metric_date} → {body}{tail}"


def _record_metrics_impl(post_id: str, channel: str, metric_date: str,
                         figures: dict | None = None, operator_message: str = "",
                         figures_line: str | None = None, confirm: bool = False,
                         engine=None) -> dict:
    """Upsert on (post_id, channel, metric_date). Omitted fields stay NULL — never zero.
    `operator_message` is stored VERBATIM as the audit trail.

    NOTHING IS WRITTEN WITHOUT confirm=true. The default call returns an echo of exactly
    what would be recorded, so a mistyped figure is caught by the operator at 21:00 rather
    than by `learn` three weeks later. Making the echo a return value rather than an
    instruction is what makes it happen every time.
    """
    eng = _get_engine(engine)
    clean = figures_from_args({"figures": figures, "figures_line": figures_line})
    if figures_line:
        parse_metrics_line(figures_line)     # surface a bad line as an error, not silence

    recorded = {k: v for k, v in clean.items() if v is not None}
    null_fields = [f for f in METRIC_FIELDS if f not in recorded]
    echo = _echo(post_id, channel, metric_date, recorded, null_fields)
    if not confirm:
        return {"preview": True, "written": False, "post_id": post_id, "channel": channel,
                "metric_date": metric_date, "will_record": recorded,
                "will_stay_unmeasured": null_fields, "echo": echo,
                "next": "read this back to the operator verbatim; call again with "
                        "confirm=true only after they agree"}
    clean = recorded
    with eng.begin() as conn:
        _log_run(conn, "record_metrics", {"post_id": post_id, "channel": channel,
                                          "metric_date": metric_date,
                                          "figures": clean})
        existing = conn.execute(text(
            "SELECT post_id FROM metrics WHERE post_id = :p AND channel = :c "
            "AND metric_date = :d"),
            {"p": post_id, "c": channel, "d": metric_date}).first()
        now = datetime.now(UTC)
        if existing is None:
            cols = ["post_id", "channel", "metric_date", "source", "operator_message",
                    "collected_at"]
            vals = [":p", ":c", ":d", "'operator_via_agent'", ":om", ":t"]
            params = {"p": post_id, "c": channel, "d": metric_date,
                      "om": operator_message, "t": now}
            for field in METRIC_FIELDS:
                if clean.get(field) is not None:
                    cols.append(field)
                    vals.append(f":{field}")
                    params[field] = clean[field]
            conn.execute(text(f"INSERT INTO metrics ({', '.join(cols)}) "
                              f"VALUES ({', '.join(vals)})"), params)
        else:
            sets = ["source = 'operator_via_agent'", "operator_message = :om",
                    "collected_at = :t"]
            params = {"p": post_id, "c": channel, "d": metric_date,
                      "om": operator_message, "t": now}
            for field in METRIC_FIELDS:
                if clean.get(field) is not None:   # None never overwrites — NULL is a value
                    sets.append(f"{field} = :{field}")
                    params[field] = clean[field]
            conn.execute(text(f"UPDATE metrics SET {', '.join(sets)} WHERE post_id = :p "
                              "AND channel = :c AND metric_date = :d"), params)
    return {"post_id": post_id, "channel": channel, "metric_date": metric_date,
            "written": True, "recorded": sorted(clean),
            "left_unmeasured": null_fields, "echo": echo,
            "source": "operator_via_agent"}


# ---------------------------------------------------------------------------------------
# recovery actions
# ---------------------------------------------------------------------------------------

def _retry_post_impl(post_id: str, engine=None) -> dict:
    """Return a FAILED post to its previous stage. Refuses if external_post_id is set —
    that means the platform accepted it and a retry would double-publish."""
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "retry_post", {"post_id": post_id})
        row = conn.execute(text(
            "SELECT status, external_post_id, media_drive_file_id, park_reason "
            "FROM posts WHERE post_id = :p"), {"p": post_id}).first()
        if row is None:
            return {"post_id": post_id, "error": "not found"}
        status, external_id, media, reason = row
        if external_id:
            return {"post_id": post_id,
                    "error": f"refused: external_post_id={external_id} — the platform "
                             "accepted this post; retrying would double-publish"}
        if status not in ("FAILED", "PARKED"):
            return {"post_id": post_id, "error": f"status is {status}, nothing to retry"}
        # A rendered post retries at publish; an unrendered one goes back to render.
        new_status = "RENDERED" if media else "APPROVED"
        conn.execute(text(
            "UPDATE posts SET status = :s, park_reason = NULL WHERE post_id = :p"),
            {"s": new_status, "p": post_id})
    return {"post_id": post_id, "status": new_status, "previous_failure": reason}


def _fulfil_wishlist_impl(post_id: str, drive_file_id: str, engine=None) -> dict:
    """Attach an asset to a PARKED post and return it to APPROVED."""
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "fulfil_wishlist", {"post_id": post_id, "drive_file_id": drive_file_id})
        post = conn.execute(text(
            "SELECT status, asset_wishlist FROM posts WHERE post_id = :p"),
            {"p": post_id}).first()
        if post is None:
            return {"post_id": post_id, "error": "not found"}
        if post[0] != "PARKED":
            return {"post_id": post_id, "error": f"status is {post[0]}, not PARKED"}
        asset = conn.execute(text(
            "SELECT status FROM assets WHERE drive_file_id = :a"),
            {"a": drive_file_id}).first()
        if asset is None or asset[0] != "active":
            return {"post_id": post_id,
                    "error": f"asset {drive_file_id} not found or not active — run assets sync"}
        wishlist = post[1]
        if isinstance(wishlist, str):
            try:
                wishlist = json.loads(wishlist)
            except (TypeError, json.JSONDecodeError):
                wishlist = []
        filled = [{**w, "fulfilled_by": drive_file_id} for w in (wishlist or [])]
        conn.execute(_jtext(
            "UPDATE posts SET status = 'APPROVED', park_reason = NULL, asset_wishlist = :w "
            "WHERE post_id = :p", "w"), {"w": filled, "p": post_id})
    return {"post_id": post_id, "status": "APPROVED", "fulfilled_by": drive_file_id}


def _acknowledge_price_table_impl(as_of: str, decision: str, engine=None) -> dict:
    """Accept (or decline) a fal-sourced price reconciliation. Writes `endpoint_prices` —
    never `config` — so the no-config-writes invariant survives reconciliation."""
    if decision not in ("accept", "decline"):
        raise ValueError(f"unknown price decision {decision!r}")
    eng = _get_engine(engine)
    now = datetime.now(UTC)
    with eng.begin() as conn:
        _log_run(conn, "acknowledge_price_table", {"as_of": as_of, "decision": decision})
        if decision == "accept":
            conn.execute(text(
                "UPDATE endpoint_prices SET acknowledged_at = :t WHERE as_of <= :a"),
                {"t": now, "a": as_of})
        rows = conn.execute(text(
            "SELECT endpoint, rate_micros, unit, source, as_of, acknowledged_at "
            "FROM endpoint_prices ORDER BY endpoint")).all()
    return {"decision": decision, "as_of": as_of,
            "table": [{"endpoint": r[0], "rate_usd": r[1] / 1_000_000, "unit": r[2],
                       "source": r[3], "as_of": str(r[4]), "acknowledged_at": str(r[5])}
                      for r in rows]}


# ---------------------------------------------------------------------------------------
# handlers — the documented contract
# ---------------------------------------------------------------------------------------

def _envelope(fn, args: dict, **kwargs) -> str:
    try:
        return json.dumps({"ok": True, "data": fn(args, **kwargs)}, default=str)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, default=str)


def read_draft_posts(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _read_draft_posts_impl(
        str(a["week_start"]), engine=kw.get("engine")), args, **kwargs)


def read_digest(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _read_digest_impl(
        a.get("date"), now=a.get("now") or kw.get("now"), engine=kw.get("engine")),
        args, **kwargs)


def deliver_video(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _deliver_video_impl(
        str(a["post_id"]), engine=kw.get("engine")), args, **kwargs)


def review_video(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _review_video_impl(
        str(a["post_id"]), str(a["decision"]), a.get("reason"), engine=kw.get("engine")),
        args, **kwargs)


def review_email(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _review_email_impl(
        str(a["post_id"]), str(a["decision"]), a.get("edits"), bool(a.get("send_test")),
        engine=kw.get("engine")), args, **kwargs)


def record_metrics(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _record_metrics_impl(
        str(a["post_id"]), str(a["channel"]), str(a["metric_date"]),
        a.get("figures") or {}, str(a.get("operator_message", "")),
        figures_line=a.get("figures_line"), confirm=bool(a.get("confirm")),
        engine=kw.get("engine")), args, **kwargs)


def retry_post(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _retry_post_impl(
        str(a["post_id"]), engine=kw.get("engine")), args, **kwargs)


def fulfil_wishlist(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _fulfil_wishlist_impl(
        str(a["post_id"]), str(a["drive_file_id"]), engine=kw.get("engine")), args, **kwargs)


def acknowledge_price_table(args: dict, **kwargs) -> str:
    return _envelope(lambda a, **kw: _acknowledge_price_table_impl(
        str(a["as_of"]), str(a["decision"]), engine=kw.get("engine")), args, **kwargs)


HANDLERS_V4: dict[str, Any] = {
    "read_draft_posts": read_draft_posts,
    "read_digest": read_digest,
    "deliver_video": deliver_video,
    "review_video": review_video,
    "review_email": review_email,
    "record_metrics": record_metrics,
    "retry_post": retry_post,
    "fulfil_wishlist": fulfil_wishlist,
    "acknowledge_price_table": acknowledge_price_table,
}
