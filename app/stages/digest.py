"""v4 §6 · C — digest preparation (job 11 body). Job 12 (D) delivers it.

The digest is the ONLY place failures surface once v4 is live, so its completeness is
safety-critical: a post that changes state and appears nowhere is invisible to the operator
permanently. `assert_complete` exists for exactly that, and a test drives it.

Five sections, in the order the operator reads them, stopping when nothing needs them:
  1 NEEDS YOU  2 WENT OUT TODAY  3 TONIGHT'S ASSET DROP  4 NUMBERS  5 SPEND & HEALTH

Idempotent on `digest_date`: re-preparing replaces the payload rather than duplicating it.
Registers with no cron — invocable by CLI and authenticated HTTPS only.

The live Brevo recipient count is read from the real endpoint AT PREPARATION TIME, never
cached and never seeded: list growth is a signal about the site, and a stale number would
hide it. If Brevo is unreachable the count is reported as unavailable — never as zero,
which would read as "the list emptied".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import all_config, get_config, net_cm_minor
from app.models import AgentRun, Digest, Metric, Order, Post, Run
from app.toolbox.pricing import micros_to_cents, price_table, stale_prices

MEASURE_FIELDS = ("impressions", "completion_rate", "watch_time_s", "saves", "shares",
                  "clicks")


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------

def _days_left(review: dict | None, default_days: int) -> int:
    # No review dict yet = review has not started, so the full window remains. Returning
    # None here rendered as "expires in Noned" in the dry run.
    if not review:
        return default_days
    expiry = review.get("expiry")
    if not expiry:
        return default_days
    try:
        when = datetime.fromisoformat(str(expiry))
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0, (when - datetime.now(UTC)).days)
    except ValueError:
        return default_days


def _email_copy(post: Post) -> dict:
    import json

    try:
        return json.loads(post.caption or "{}")
    except json.JSONDecodeError:
        return {}


def _live_recipient_count(brevo) -> int | None:
    """Real endpoint, at preparation time. None = unavailable, never 0."""
    if brevo is None:
        return None
    try:
        return int(brevo.get_list_count())
    except Exception:
        return None


# ---------------------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------------------

def _needs_you(session: Session, brevo, target: date, cfg: dict) -> dict:
    video_expiry = int(cfg.get("video_review_expiry_days", 3))
    email_expiry = int(cfg.get("email_review_expiry_days", 3))

    videos, emails = [], []
    for post in session.execute(
        select(Post).where(Post.status == "RENDERED").order_by(Post.post_id)
    ).scalars():
        review = post.video_review or {}
        if post.channel == "email":
            copy = _email_copy(post)
            emails.append({
                "post_id": post.post_id, "slot": post.slot,
                "subject": copy.get("subject"), "headline": copy.get("headline"),
                "body_copy": copy.get("body_copy"), "cta_text": copy.get("cta_text"),
                "story_block": copy.get("story_block"),
                "hero_image": post.media_drive_file_id,
                "tracked_url": post.tracked_url,
                "days_to_expiry": _days_left(post.email_review, email_expiry),
            })
        elif (post.media_drive_file_id or "").endswith(".mp4") or review.get("public_url"):
            videos.append({
                "post_id": post.post_id, "channel": post.channel, "slot": post.slot,
                "caption": post.caption,
                "public_url": review.get("public_url"),
                "delivered": bool(review.get("telegram_message_id")),
                "days_to_expiry": _days_left(review, video_expiry),
            })

    published = list(session.execute(
        select(Post).where(Post.status == "PUBLISHED")).scalars())
    unmeasured = [
        {"post_id": p.post_id, "channel": p.channel,
         "posted_at": p.posted_at.isoformat() if p.posted_at else None}
        for p in published
        if session.get(Metric, (p.post_id, p.channel, target)) is None
    ]

    failures = [
        {"post_id": p.post_id, "channel": p.channel, "reason": p.park_reason}
        for p in session.execute(
            select(Post).where(Post.status == "FAILED").order_by(Post.post_id)).scalars()
    ]
    parked = [
        {"post_id": p.post_id, "channel": p.channel, "reason": p.park_reason,
         "wishlist": p.asset_wishlist or []}
        for p in session.execute(
            select(Post).where(Post.status == "PARKED").order_by(Post.post_id)).scalars()
    ]

    last_doctor = get_config(session, "last_doctor", None) or {}
    doctor_red = [c for c in last_doctor.get("checks", [])
                  if c.get("status") == "RED"]

    from app.scheduler import sweep_orphaned_slots

    orphans = sweep_orphaned_slots(session)

    section = {
        "video_review": videos, "email_review": emails, "unmeasured": unmeasured,
        "failures": failures, "parked": parked, "doctor_red": doctor_red,
        "orphaned_slots": orphans,
        "brevo_list_count": _live_recipient_count(brevo),
        "doctor_last_run": last_doctor.get("at"),
    }
    section["empty"] = not any(
        section[k] for k in ("video_review", "email_review", "unmeasured", "failures",
                             "parked", "doctor_red", "orphaned_slots"))
    return section


def _went_out_today(session: Session, target: date) -> list[dict]:
    out = []
    for post in session.execute(
        select(Post).where(Post.status == "PUBLISHED").order_by(Post.posted_at)
    ).scalars():
        if post.posted_at and post.posted_at.date() == target:
            out.append({"post_id": post.post_id, "channel": post.channel, "slot": post.slot,
                        "permalink": post.external_post_id, "tracked_url": post.tracked_url})
    return out


def _asset_drop(session: Session) -> list[dict]:
    """Exact Drive folders to shoot into tonight, from open wishlists."""
    by_folder: dict[str, list[str]] = {}
    for post in session.execute(
        select(Post).where(Post.status == "PARKED").order_by(Post.post_id)
    ).scalars():
        for entry in post.asset_wishlist or []:
            if entry.get("fulfilled_by"):
                continue
            folder = entry.get("target_folder", "?")
            by_folder.setdefault(folder, []).append(
                f"{post.post_id}: {entry.get('description', '')}")
    return [{"folder": f, "wants": w} for f, w in sorted(by_folder.items())]


def _numbers(session: Session, target: date, cfg: dict) -> dict:
    """Yesterday's revenue and engagement — SEPARATE blocks, never blended. Unmeasured is
    labelled unmeasured, never zero (lane rule + stale ≠ zero)."""
    revenue: dict = {"by_currency": {}, "unattributed": 0, "attributed": 0}
    for order in session.execute(select(Order)).scalars():
        if not order.occurred_at or order.occurred_at.date() != target:
            continue
        if order.post_id:
            revenue["attributed"] += 1
            cur = order.currency or "?"
            bucket = revenue["by_currency"].setdefault(cur, {"orders": 0, "net_cm_minor": 0})
            bucket["orders"] += 1
            try:
                bucket["net_cm_minor"] += net_cm_minor(cur, cfg)
            except ValueError:
                pass
        else:
            revenue["unattributed"] += 1

    engagement: dict = {"measured": {}, "unmeasured_posts": []}
    for metric in session.execute(
        select(Metric).where(Metric.metric_date == target)).scalars():
        recorded = {f: float(getattr(metric, f)) for f in MEASURE_FIELDS
                    if getattr(metric, f) is not None}
        if recorded:
            engagement["measured"][metric.post_id] = recorded
    for post in session.execute(
        select(Post).where(Post.status == "PUBLISHED")).scalars():
        if post.post_id not in engagement["measured"]:
            engagement["unmeasured_posts"].append(post.post_id)

    return {"date": str(target), "revenue": revenue, "engagement": engagement}


def _spend_and_health(session: Session, brevo, cfg: dict, list_count: int | None) -> dict:
    since = datetime.now(UTC) - timedelta(days=7)

    fal_micros = 0
    for run in session.execute(select(Run)).scalars():
        started = run.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if run.cost_micros and started and started >= since:
            fal_micros += int(run.cost_micros)

    agent_cents = 0
    for arun in session.execute(select(AgentRun)).scalars():
        started = arun.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if arun.cost_cents and started and started >= since:
            agent_cents += int(arun.cost_cents)

    # System CAC — organic only, so this is HEALTH, never a kill criterion (§E1). The only
    # marginal cost of an organic post is what it cost to make.
    attributed = {"SGD": 0, "MYR": 0}
    for order in session.execute(select(Order)).scalars():
        if order.post_id and order.currency in attributed:
            attributed[order.currency] += 1
    # Spend is USD; orders are SGD and MYR. Dividing the SAME total spend by each
    # currency's order count separately produces two numbers that look currency-specific
    # and are not — the dry run rendered "SGD=49.66, MYR=49.66", which is a category error
    # wearing a decimal point. Report ONE honest figure: production cost per attributed
    # order, with the per-currency order counts alongside it, uncombined.
    total_spend_cents = micros_to_cents(fal_micros) + agent_cents
    total_attributed = sum(attributed.values())
    cac = {
        "cost_per_attributed_order_cents": (round(total_spend_cents / total_attributed, 2)
                                            if total_attributed else None),
        "attributed_orders": attributed,
        "basis": "USD production spend (fal + agent) ÷ attributed orders; currencies are "
                 "never converted or combined",
    }

    return {
        "fal_spend_cents_wtd": round(micros_to_cents(fal_micros), 2),
        "render_run_cap_cents": cfg.get("render_run_cap_cents"),
        "agent_spend_cents_wtd": agent_cents,
        "agent_weekly_cap_minor": cfg.get("agent_weekly_cap_minor"),
        "system_cac_cents": cac,
        "cac_is_health_only": True,
        "brevo_list_count": list_count,
        "email_min_recipients": cfg.get("email_min_recipients"),
        "price_table": price_table(session),
        "stale_prices": stale_prices(session),
        # Reported EVERY night while no search backend is configured (§7·A5).
        "scouting": get_config(session, "scouting_status", None)
                    or {"available": False,
                        "reason": "no search backend probed yet (Stage 2c wires the boot probe)"},
    }


# ---------------------------------------------------------------------------------------
# preparation
# ---------------------------------------------------------------------------------------

def build_payload(session: Session, brevo=None, target: date | None = None) -> dict:
    target = target or (datetime.now(UTC).date() - timedelta(days=1))
    cfg = all_config(session)
    needs = _needs_you(session, brevo, target, cfg)
    return {
        "date": str(target),
        "prepared_at": datetime.now(UTC).isoformat(),
        "needs_you": needs,
        "went_out_today": _went_out_today(session, target),
        "asset_drop": _asset_drop(session),
        "numbers": _numbers(session, target, cfg),
        "spend_health": _spend_and_health(session, brevo, cfg,
                                          needs.get("brevo_list_count")),
    }


def assert_complete(session: Session, payload: dict, since_hours: int = 24) -> list[str]:
    """Digest completeness is safety-critical: the digest is the only place failures
    surface. Every post whose state changed in the window must appear SOMEWHERE in the
    payload. Returns the post_ids that do not — empty list means complete."""
    import json

    blob = json.dumps(payload, default=str)
    cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
    missing = []
    for post in session.execute(select(Post)).scalars():
        updated = post.updated_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated is None or updated < cutoff:
            continue
        if post.status in ("PARKED", "FAILED", "PUBLISHED", "RENDERED", "APPROVED_TO_SEND") \
                and post.post_id not in blob:
            missing.append(post.post_id)
    return missing


def prepare_digest(session: Session, brevo=None, target: date | None = None,
                   log=print) -> dict:
    """Job 11 body. Idempotent on digest_date."""
    target = target or (datetime.now(UTC).date() - timedelta(days=1))
    payload = build_payload(session, brevo=brevo, target=target)

    missing = assert_complete(session, payload)
    if missing:
        # Never silently drop a state change — surface it inside the digest itself.
        payload["completeness_warning"] = {
            "posts_missing_from_digest": missing,
            "note": "these changed state in the last 24h and are not represented — a post "
                    "that appears nowhere is invisible to the operator permanently",
        }
        log(f"digest: COMPLETENESS WARNING — {len(missing)} post(s) unrepresented: {missing}")

    row = session.execute(
        select(Digest).where(Digest.digest_date == target)).scalar_one_or_none()
    if row is None:
        session.add(Digest(digest_date=target, payload=payload))
    else:
        row.payload = payload          # idempotent: replace, never duplicate
        row.delivered_at = None        # re-prepared → re-deliverable
    session.flush()
    log(f"digest {target}: prepared "
        f"({'nothing needs you' if payload['needs_you']['empty'] else 'ACTION REQUIRED'})")
    return payload


# ---------------------------------------------------------------------------------------
# rendering — the exact text the operator reads. D delivers this; it is produced here so a
# dry run can be judged before the transport exists.
# ---------------------------------------------------------------------------------------

def render_digest_text(payload: dict) -> str:
    p = payload
    needs = p["needs_you"]
    lines: list[str] = [f"HERMES · {p['date']}", ""]

    lines.append("━━ 1 · NEEDS YOU ━━")
    if needs["empty"]:
        lines.append("Nothing needs you tonight. 👍")
    else:
        for v in needs["video_review"]:
            lines.append(f"🎬 VIDEO REVIEW — {v['post_id']} · {v['channel']} · slot {v['slot']}")
            lines.append(f"   {(v['caption'] or '')[:120]}")
            lines.append(f"   expires in {v['days_to_expiry']}d · approve / reject / rerender")
        for e in needs["email_review"]:
            count = needs.get("brevo_list_count")
            shown = f"{count} recipients" if count is not None else "recipient count UNAVAILABLE"
            lines.append(f"✉️  EMAIL REVIEW — {e['post_id']} · slot {e['slot']} · list 3: {shown}")
            lines.append(f"   subject:  {e['subject']}")
            lines.append(f"   headline: {e['headline']}")
            lines.append(f"   body:     {(e['body_copy'] or '')[:160]}")
            lines.append(f"   cta:      {e['cta_text']}")
            lines.append(f"   story:    {(e['story_block'] or '')[:120]}")
            lines.append(f"   hero:     {e['hero_image']}")
            lines.append(f"   link:     {e['tracked_url']}")
            lines.append(f"   expires in {e['days_to_expiry']}d · approve / edit / reject / test send")
        if needs["unmeasured"]:
            ids = ", ".join(u["post_id"] for u in needs["unmeasured"])
            lines.append(f"📊 METRICS — {len(needs['unmeasured'])} unmeasured: {ids}")
            lines.append("   reply with the figures and I'll record them verbatim")
        for f in needs["failures"]:
            lines.append(f"🔁 RETRY — {f['post_id']} · {f['channel']} · {(f['reason'] or '')[:80]}")
        for pk in needs["parked"]:
            folders = ", ".join(w.get("target_folder", "?") for w in pk["wishlist"])
            lines.append(f"📦 PARKED — {pk['post_id']} · needs {folders}")
        for d in needs["doctor_red"]:
            lines.append(f"🚨 DOCTOR RED — {d.get('name')}: {d.get('detail')}")
        for o in needs["orphaned_slots"]:
            lines.append(f"⚠️  ORPHAN SLOT — {o['post_id']}: {o['reason']}")

    lines += ["", "━━ 2 · WENT OUT TODAY ━━"]
    if not p["went_out_today"]:
        lines.append("nothing published today")
    for w in p["went_out_today"]:
        lines.append(f"✅ {w['post_id']} · {w['channel']} · {w['slot']} · {w['permalink']}")

    lines += ["", "━━ 3 · TONIGHT'S ASSET DROP ━━"]
    if not p["asset_drop"]:
        lines.append("nothing on the wishlist — the bank is ahead of the ideas")
    for a in p["asset_drop"]:
        lines.append(f"📁 {a['folder']}/")
        for want in a["wants"]:
            lines.append(f"   {want}")

    n = p["numbers"]
    lines += ["", "━━ 4 · NUMBERS ━━", "REVENUE (orders only)"]
    if not n["revenue"]["by_currency"]:
        lines.append("   no attributed orders")
    for cur, b in sorted(n["revenue"]["by_currency"].items()):
        lines.append(f"   {cur}: {b['orders']} orders · net CM {b['net_cm_minor']} minor")
    lines.append(f"   unattributed: {n['revenue']['unattributed']}")
    lines.append("ENGAGEMENT (events + metrics only)")
    if not n["engagement"]["measured"]:
        lines.append("   nothing measured")
    for pid, m in sorted(n["engagement"]["measured"].items()):
        lines.append("   " + pid + ": " + ", ".join(f"{k}={int(v)}" for k, v in m.items()))
    if n["engagement"]["unmeasured_posts"]:
        lines.append(f"   unmeasured (not zero): {', '.join(n['engagement']['unmeasured_posts'])}")

    s = p["spend_health"]
    lines += ["", "━━ 5 · SPEND & HEALTH ━━"]
    lines.append(f"fal (week): {s['fal_spend_cents_wtd']}¢ · cap {s['render_run_cap_cents']}¢/run")
    lines.append(f"agent (week): {s['agent_spend_cents_wtd']}¢ · cap {s['agent_weekly_cap_minor']}¢")
    cac = s["system_cac_cents"]
    per_order = cac.get("cost_per_attributed_order_cents")
    counts = ", ".join(f"{c} {n}" for c, n in sorted(cac.get("attributed_orders", {}).items()))
    lines.append(
        "production cost per attributed order (health only, never a kill rule): "
        + (f"{per_order}¢" if per_order is not None else "n/a — no attributed orders")
        + (f"  ·  attributed: {counts}" if counts else ""))
    count = s["brevo_list_count"]
    if count is None:
        lines.append("brevo list 3: UNAVAILABLE (not zero — the endpoint did not answer)")
    else:
        below = " — below measurement threshold" if count < (s["email_min_recipients"] or 0) else ""
        lines.append(f"brevo list 3: {count} recipients{below}")
    if s["stale_prices"]:
        lines.append(f"⚠️  price table stale: {', '.join(s['stale_prices'])}")
    sc = s["scouting"]
    lines.append(f"scouting: {'available' if sc.get('available') else 'UNAVAILABLE'} — {sc.get('reason', '')}")
    if p.get("completeness_warning"):
        lines.append("")
        lines.append(f"🛑 {p['completeness_warning']['note']}")
        lines.append(f"   {p['completeness_warning']['posts_missing_from_digest']}")
    return "\n".join(lines)
