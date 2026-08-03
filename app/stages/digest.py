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

# Telegram hard limit on one message. The digest is split on SECTION boundaries before
# delivery — a truncated digest is a post that becomes invisible forever.
TELEGRAM_MESSAGE_LIMIT = 4096


# ---------------------------------------------------------------------------------------
# money rendering — minor units are the STORAGE invariant and never change; formatting
# happens here, at the render boundary, because the digest is a human surface and
# "net CM 21200 minor" asks a human at 21:00 to divide by 100 in their head.
# ---------------------------------------------------------------------------------------

CURRENCY_SYMBOLS = {"SGD": "S$", "MYR": "RM", "USD": "US$"}


def format_money_minor(currency: str, minor: int) -> str:
    """21200, 'MYR' → 'RM212.00'. Integer arithmetic upstream is untouched: this divides
    only to display, and an unknown currency renders its code rather than guessing."""
    minor = int(minor)
    sign = "-" if minor < 0 else ""
    whole, cents = divmod(abs(minor), 100)
    symbol = CURRENCY_SYMBOLS.get(currency)
    body = f"{whole:,}.{cents:02d}"
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{currency} {body}"


def format_usd_cents(cents: float) -> str:
    """USD spend is metered in cents (fal micros → cents; agent cents). Sub-cent amounts
    render as '<US$0.01' rather than as US$0.00, which would read as 'free'. The exact
    figure stays in the payload — this rounds for display only."""
    if cents and abs(cents) < 1:
        return "<US$0.01"
    return format_money_minor("USD", int(round(cents)))


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


def _price_status(session: Session) -> dict:
    """§6 requires SPEND & HEALTH to flag a stale price table or a failed fal pull. An
    ABSENT warning reads as 'no problem' — the config-silence failure class, in the one
    place designed to surface problems. So a line is emitted every night, always."""
    table = price_table(session)
    stale = stale_prices(session)
    if not table:
        return {"state": "empty", "stale": stale, "as_of": None, "reconciled_at": None,
                "note": "EMPTY — no endpoint is priced, so every render will refuse"}
    as_of = min((r["as_of"] for r in table if r["as_of"]), default=None)
    acked = [r["acknowledged_at"] for r in table if r["acknowledged_at"]]
    reconciled_at = max(acked) if acked else None
    sources = {r["source"] for r in table}
    if stale:
        state = "stale"
    elif reconciled_at:
        state = "reconciled"
    else:
        state = "never_reconciled"
    return {
        "state": state,
        "stale": stale,
        "as_of": (as_of or "")[:10] or None,
        "reconciled_at": (reconciled_at or "")[:10] or None,
        "sources": sorted(sources),
        # Accurate the moment 2c lands the fal pull: reconciliation sets acknowledged_at.
        "note": ("seeded {as_of}, never reconciled against fal"
                 if state == "never_reconciled" else ""),
    }


def _spend_and_health(session: Session, brevo, cfg: dict, list_count: int | None) -> dict:
    since = datetime.now(UTC) - timedelta(days=7)

    # Week-to-date total AND the most recent single run, kept apart. The run cap is a
    # PER-RUN limit; setting a week-to-date figure beside it is the same class of error the
    # per-megapixel correction fixed in the price table — a number displayed against the
    # wrong denominator is how a cap silently stops meaning what it was set to mean.
    fal_micros = 0
    fal_runs: list[tuple[datetime, int]] = []
    for run in session.execute(select(Run)).scalars():
        started = run.started_at
        if started and started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if run.cost_micros and started and started >= since:
            fal_micros += int(run.cost_micros)
            fal_runs.append((started, int(run.cost_micros)))
    fal_runs.sort()
    last_run = fal_runs[-1] if fal_runs else None

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
        "fal_render_runs_this_week": len(fal_runs),
        "fal_last_run_cents": round(micros_to_cents(last_run[1]), 2) if last_run else None,
        "fal_last_run_at": last_run[0].isoformat() if last_run else None,
        "render_run_cap_cents": cfg.get("render_run_cap_cents"),
        "agent_spend_cents_wtd": agent_cents,
        "agent_weekly_cap_minor": cfg.get("agent_weekly_cap_minor"),
        "system_cac_cents": cac,
        "cac_is_health_only": True,
        "brevo_list_count": list_count,
        "email_min_recipients": cfg.get("email_min_recipients"),
        "price_table": price_table(session),
        "price_status": _price_status(session),
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

    # The transport split is computed here, in tested code, and carried on the payload.
    # `read_digest` hands the brain these messages to send verbatim, in order — so the
    # 4096-character limit and "NEEDS YOU first" are properties of the system, not of the
    # model remembering an instruction.
    payload["messages"] = split_for_telegram(render_digest_text(payload))

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
        plural = "order" if b["orders"] == 1 else "orders"
        lines.append(f"   {cur}: {b['orders']} {plural} · net CM "
                     f"{format_money_minor(cur, b['net_cm_minor'])}")
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
    # Per-RUN spend against the per-RUN cap; week-to-date reported separately, never as if
    # it were the capped quantity.
    runs = s.get("fal_render_runs_this_week", 0)
    cap = format_usd_cents(s["render_run_cap_cents"] or 0)
    if not runs:
        lines.append(f"fal · no render run this week · run cap {cap}")
    else:
        at = (s.get("fal_last_run_at") or "")[:16].replace("T", " ")
        lines.append(f"fal · last render run ({at}): "
                     f"{format_usd_cents(s['fal_last_run_cents'] or 0)} · run cap {cap}")
        if runs == 1:
            lines.append(f"fal · week to date: {format_usd_cents(s['fal_spend_cents_wtd'])} "
                         "— the same figure; there was only one render run this week")
        else:
            lines.append(f"fal · week to date: {format_usd_cents(s['fal_spend_cents_wtd'])} "
                         f"across {runs} render runs (no weekly fal cap — the cap is per run)")
    lines.append(f"agent · week to date: {format_usd_cents(s['agent_spend_cents_wtd'])} · "
                 f"weekly cap {format_usd_cents(s['agent_weekly_cap_minor'] or 0)}")
    cac = s["system_cac_cents"]
    per_order = cac.get("cost_per_attributed_order_cents")
    counts = ", ".join(f"{c} {n}" for c, n in sorted(cac.get("attributed_orders", {}).items()))
    lines.append(
        "production cost per attributed order (health only, never a kill rule): "
        + (format_usd_cents(per_order) if per_order is not None
           else "n/a — no attributed orders")
        + (f"  ·  attributed: {counts}" if counts else ""))
    count = s["brevo_list_count"]
    if count is None:
        lines.append("brevo list 3: UNAVAILABLE (not zero — the endpoint did not answer)")
    else:
        below = " — below measurement threshold" if count < (s["email_min_recipients"] or 0) else ""
        lines.append(f"brevo list 3: {count} recipients{below}")
    # Always a line — an absent warning reads as "no problem".
    ps = s.get("price_status") or {}
    state = ps.get("state")
    if state == "stale":
        lines.append(f"⚠️  price table STALE: {', '.join(ps.get('stale', []))} "
                     f"(as of {ps.get('as_of')}) — renders are costed against old rates")
    elif state == "empty":
        lines.append(f"🚨 price table: {ps.get('note')}")
    elif state == "reconciled":
        lines.append(f"price table: reconciled against fal {ps.get('reconciled_at')}")
    else:
        lines.append(f"price table: seeded {ps.get('as_of')}, never reconciled against fal")
    sc = s["scouting"]
    lines.append(f"scouting: {'available' if sc.get('available') else 'UNAVAILABLE'} — {sc.get('reason', '')}")
    if p.get("completeness_warning"):
        lines.append("")
        lines.append(f"🛑 {p['completeness_warning']['note']}")
        lines.append(f"   {p['completeness_warning']['posts_missing_from_digest']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# transport — the payload does not know about Telegram's 4096-character message limit, and
# a silently truncated digest is a post that becomes invisible forever. Splitting is done
# HERE, deterministically, and the resulting messages are stored on the payload so the
# brain sends them verbatim in order. Making this the model's job would make "NEEDS YOU is
# always in the first message" a matter of compliance rather than a property of the code.
# ---------------------------------------------------------------------------------------

SECTION_MARK = "━━"
_PREFIX_RESERVE = 48       # room for the "HERMES · YYYY-MM-DD (2/3)" continuation header


def _section_blocks(text: str) -> list[list[str]]:
    """Preamble first, then one block per '━━ n · SECTION ━━' heading."""
    blocks: list[list[str]] = [[]]
    for line in text.split("\n"):
        if line.startswith(SECTION_MARK) and blocks[-1]:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return [b for b in blocks if b]


def _sub_blocks(lines: list[str]) -> list[list[str]]:
    """Item boundaries inside a section: a new item starts on a non-indented line; its
    continuation lines are indented, and blank lines stay with what precedes them."""
    out: list[list[str]] = []
    for line in lines:
        if out and (line.startswith((" ", "\t")) or not line.strip()):
            out[-1].append(line)
        else:
            out.append([line])
    return out


def _hard_split(line: str, budget: int) -> list[str]:
    """Last resort for a single line longer than a whole message. Never reached by real
    content (captions are capped well below this) but silent loss is the one outcome that
    must be impossible, so it wraps rather than truncates."""
    return [line[i:i + budget] for i in range(0, len(line), budget)] or [""]


def _split_oversized_section(block: list[str], budget: int) -> list[str]:
    header, body = block[0], block[1:]
    pieces: list[str] = []
    current = [header]
    for item in _sub_blocks(body):
        candidate = current + item
        if len("\n".join(candidate)) <= budget:
            current = candidate
            continue
        if len(current) > 1:
            pieces.append("\n".join(current))
            current = [f"{header} (continued)"]
        if len("\n".join(current + item)) <= budget:
            current = current + item
            continue
        line_budget = budget - len(header) - 20   # a single item too big for one message
        for line in item:
            parts = _hard_split(line, line_budget) if len(line) > line_budget else [line]
            for part in parts:
                if len("\n".join(current + [part])) > budget:
                    pieces.append("\n".join(current))
                    current = [f"{header} (continued)"]
                current.append(part)
    if len(current) > 1 or not pieces:
        pieces.append("\n".join(current))
    return pieces


def split_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split on SECTION boundaries only. NEEDS YOU is always in the first message because
    packing is strictly in reading order and the preamble is tiny; a section too large for
    one message splits at item boundaries, never mid-line, and says '(continued)'."""
    if len(text) <= limit:
        return [text]
    budget = limit - _PREFIX_RESERVE
    header_line = text.split("\n", 1)[0]

    chunks: list[str] = []
    current = ""
    for block in _section_blocks(text):
        body = "\n".join(block)
        joined = f"{current}\n{body}" if current else body
        if len(joined) <= budget:
            current = joined
        elif len(body) <= budget:
            if current:
                chunks.append(current)
            current = body
        else:
            pieces = _split_oversized_section(block, budget)
            # Keep whatever is pending attached to the FIRST piece where it fits — this is
            # what keeps the two-line preamble in front of an oversized NEEDS YOU instead
            # of spending a whole message on it.
            if current and len(current) + 1 + len(pieces[0]) <= budget:
                pieces[0] = f"{current}\n{pieces[0]}"
            elif current:
                chunks.append(current)
            current = ""
            chunks.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        chunks.append(current)

    total = len(chunks)
    return [c if i == 0 else f"{header_line} ({i + 1}/{total})\n{c}"
            for i, c in enumerate(chunks)]
