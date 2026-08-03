"""v4 seam additions — nine new tools (six + nine = fifteen).

Same contract as tools.py: `(args: dict, **kwargs) -> str`, JSON envelope always, never
raises. Self-contained — sqlalchemy textual SQL plus httpx for the two tools that must
reach Telegram/Brevo. No tool writes `orders`, `events`, or `config`; `metrics` becomes
writable by TRANSCRIPTION ONLY (see record_metrics + the hook in tools.py).

Tool count note: the v4 prompt specified fourteen and declared `read_digest` READ ONLY.
Delivering a video natively AND recording the delivery receipt cannot both live in a
read-only tool, so the operator elected the fifteenth tool — `deliver_video` — rather than
weakening read_digest. That is the cleaner shape: read stays read, delivery is explicit.

Video delivery design: the brain has no Google Drive client and Telegram cannot fetch a
Drive share link (they are HTML viewer pages, not media). Job 11 therefore publishes a
temporary public URL for each pending video into the digest payload — the same
fal-storage pattern already used in production for Brevo hero images — and `deliver_video`
hands that URL to Telegram `sendVideo`. No new dependency in the brain image, no Drive
credentials needed here, and the delivery receipt (`message_id`) is what `review_video`
refuses to proceed without.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime, timedelta
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

def _read_draft_posts_impl(week_start: str, engine=None) -> list[dict]:
    """Every DRAFT for the week with its full creative genome — closes gap A1.

    The gate previously had to fish drafts out of v_brief's LIMIT 14 window, which was
    undesigned and breaks as cadence rises. This is the designed path.
    """
    eng = _get_engine(engine)
    with eng.begin() as conn:
        _log_run(conn, "read_draft_posts", {"week_start": week_start})
        rows = conn.execute(text(
            "SELECT post_id, channel, status, angle, hook, cta_type, cta_placement, "
            "keywords, slot, plan_source, tracked_url FROM posts "
            "WHERE week_start = :w AND status = 'DRAFT' ORDER BY channel, slot, post_id"),
            {"w": week_start}).all()
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
                    "tracked_url": r[10]})
    return out


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
    target = str(digest_date or date.today())
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
            return {"date": target, "prepared": False, "deliver": False,
                    "note": "no digest prepared for this date — job 11 has not run"}
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

def _deliver_video_impl(post_id: str, engine=None) -> dict:
    """Send the rendered video into the chat as a NATIVE Telegram video message and record
    the delivery message_id. `review_video` refuses without that receipt, so nobody can
    approve a video they were never shown.

    Telegram refusing the upload PARKs the post — that refusal is independent evidence the
    file is malformed and is more trustworthy than our own ffprobe pass.
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
        review = json.loads(review) if isinstance(review, str) else (review or {})
        if review.get("telegram_message_id"):
            return {"post_id": post_id, "already_delivered": True,
                    "telegram_message_id": review["telegram_message_id"]}
        public_url = review.get("public_url")
        if not public_url:
            return {"post_id": post_id,
                    "error": "no public_url on the post — job 11 prepares it for pending videos"}

    cap = f"{post_id} · {channel} · slot {slot}\n{(caption or '')[:900]}"
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendVideo",
            data={"chat_id": chat_id, "video": public_url, "caption": cap,
                  "supports_streaming": True},
            timeout=180,
        )
        body = resp.json()
    except Exception as e:  # network/timeout — do NOT park on a transient error
        return {"post_id": post_id, "error": f"telegram send failed: {type(e).__name__}: {e}",
                "parked": False}

    now = datetime.now(UTC)
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
            "expires_in_days": expiry_days}


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
        review = json.loads(review) if isinstance(review, str) else (review or {})
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
        review = json.loads(review) if isinstance(review, str) else (review or {})
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
