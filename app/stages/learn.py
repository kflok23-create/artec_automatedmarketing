"""LEARN — scores last week's levers against KPI_WEIGHTS under the lane rule, computes CAC
and NET CM per channel, applies kill lines, writes `learnings` verdicts.

Lane rule: revenue figures come only from `orders`, engagement only from `events`+`metrics`.
The weighted score blends *normalized, unitless* lane scores inside this function only
(DECISIONS.md #6); raw money never meets a raw metric. Currencies are never converted —
CAC is judged against the kill line of its own currency. Cold start (zero history) emits an
explicit "insufficient data" verdict, never a crash.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import all_config, kill_line_minor, net_cm_minor
from app.models import Event, Learning, Metric, Order, Post

LEVERS = ("channel", "angle", "hook", "cta_type", "cta_placement", "slot")

EVENT_POINTS = {"page_view": 1, "add_to_cart": 3, "checkout_start": 5}


def last_week_start(today: date | None = None) -> date:
    today = today or datetime.now(UTC).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def render_brief(session: Session) -> str:
    rows = session.execute(text("SELECT section, line FROM v_brief")).all()
    return "\n".join(f"[{section}] {line}" for section, line in rows)


def _engagement_raw(metrics: list[Metric]) -> float | None:
    """A single unitless engagement figure per post; None when nothing was measured.
    NULL fields are skipped, never coerced to 0 (STALE ≠ ZERO)."""
    measured = False
    score = 0.0
    for m in metrics:
        for field, weight in (("impressions", 0.001), ("saves", 5), ("shares", 10), ("clicks", 2)):
            v = getattr(m, field)
            if v is not None:
                measured = True
                score += float(v) * weight
        if m.completion_rate is not None:
            measured = True
            score += float(m.completion_rate) * 50
    return score if measured else None


def _normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


def learn(session: Session, llm, week_start: date | None = None, log=print) -> dict:
    week = week_start or last_week_start()
    cfg = all_config(session)
    weights = cfg.get("kpi_weights", {"engagement": 0.3, "traffic": 0.3, "sales": 0.4})

    # WITHDRAWN POSTS ARE EXCLUDED FROM SCORING AND REPORTED AS WITHDRAWN — never as zero.
    #
    # post_1488 (facebook) and post_1489 (linkedin) published to a PERSONAL profile before
    # page targeting existed, and the operator removed them at source. Their rows still say
    # PUBLISHED, correctly: they were. But scoring them would tell `learn` that facebook and
    # linkedin published and earned nothing, marking down two channels for a targeting
    # defect that has nothing to do with the creative.
    #
    # That is the `email_min_recipients` trap arriving on two other channels, and it is
    # invariant 2 in a new costume: stale is not zero, and "removed at source" is not zero
    # either. A withdrawn post is neither a success nor a failure — it is an absence of
    # evidence, and the only honest thing to do with it is say so and leave it out.
    all_published = list(
        session.execute(
            select(Post).where(Post.week_start == week, Post.status == "PUBLISHED")
        ).scalars()
    )
    withdrawn = [p for p in all_published if getattr(p, "withdrawn_at", None) is not None]
    posts = [p for p in all_published if getattr(p, "withdrawn_at", None) is None]
    if withdrawn:
        log(f"learn {week}: EXCLUDING {len(withdrawn)} withdrawn post(s) from scoring — "
            f"{[p.post_id for p in withdrawn]}. Withdrawn is not zero; scoring these would "
            f"mark down {sorted({p.channel for p in withdrawn})} for a defect in targeting, "
            f"not in the creative.")

    # Idempotent: re-running a week replaces that week's verdicts.
    session.execute(delete(Learning).where(Learning.week_start == week))

    if not posts:
        session.add(
            Learning(
                week_start=week, lever="cold_start", lever_value=None, kpi="all",
                score=None, sample_size=0, verdict="insufficient data",
            )
        )
        session.flush()
        log(f"learn {week}: no published posts — wrote 'insufficient data' verdict")
        return {"week": str(week), "verdicts": 1, "cold_start": True}

    post_ids = [p.post_id for p in posts]
    metrics_by_post: dict[str, list[Metric]] = defaultdict(list)
    for m in session.execute(select(Metric).where(Metric.post_id.in_(post_ids))).scalars():
        metrics_by_post[m.post_id].append(m)
    events_by_post: dict[str, int] = defaultdict(int)
    for e in session.execute(select(Event).where(Event.post_id.in_(post_ids))).scalars():
        events_by_post[e.post_id] += EVENT_POINTS.get(e.event_type or "", 1)
    orders_by_post: dict[str, list[Order]] = defaultdict(list)
    for o in session.execute(select(Order).where(Order.post_id.in_(post_ids))).scalars():
        orders_by_post[o.post_id].append(o)

    written = 0
    for lever in LEVERS:
        groups: dict[str, list[Post]] = defaultdict(list)
        for p in posts:
            value = p.channel if lever == "channel" else getattr(p, lever)
            if value:
                groups[str(value)].append(p)
        if not groups:
            continue

        eng: dict[str, float] = {}
        traffic: dict[str, float] = {}
        sales_sgd: dict[str, float] = {}
        sales_myr: dict[str, float] = {}
        for value, members in groups.items():
            eng_vals = [s for p in members if (s := _engagement_raw(metrics_by_post[p.post_id])) is not None]
            if eng_vals:
                eng[value] = sum(eng_vals)
            traffic[value] = float(sum(events_by_post[p.post_id] for p in members))
            for p in members:
                for o in orders_by_post[p.post_id]:
                    if o.currency == "SGD":
                        sales_sgd[value] = sales_sgd.get(value, 0.0) + net_cm_minor("SGD", cfg)
                    elif o.currency == "MYR":
                        sales_myr[value] = sales_myr.get(value, 0.0) + net_cm_minor("MYR", cfg)

        eng_n, traffic_n = _normalize(eng), _normalize(traffic)
        sgd_n, myr_n = _normalize(sales_sgd), _normalize(sales_myr)
        for value, members in groups.items():
            # sales: mean of the per-currency NORMALIZED (unitless) scores that exist —
            # markets stay separate in money terms, only unitless ranks combine.
            sale_parts = [n[value] for n in (sgd_n, myr_n) if value in n]
            sales_score = sum(sale_parts) / len(sale_parts) if sale_parts else 0.0
            score = (
                weights["engagement"] * eng_n.get(value, 0.0)
                + weights["traffic"] * traffic_n.get(value, 0.0)
                + weights["sales"] * sales_score
            )
            sample = len(members)
            verdict = "test" if sample < 2 else ("keep" if score >= 0.5 else "test")
            session.add(
                Learning(
                    week_start=week, lever=lever, lever_value=value, kpi="weighted",
                    score=round(score, 4), sample_size=sample, verdict=verdict,
                )
            )
            written += 1

    # CAC + kill lines, per channel, per currency — only when the operator seeded spend.
    spend_cfg = cfg.get("weekly_spend_minor") or {}
    for channel in {p.channel for p in posts}:
        spend = spend_cfg.get(channel)
        channel_orders = [o for p in posts if p.channel == channel for o in orders_by_post[p.post_id]]
        if not spend:
            session.add(
                Learning(
                    week_start=week, lever="channel_cac", lever_value=channel, kpi="cac",
                    score=None, sample_size=len(channel_orders), verdict="unmeasurable (no spend configured)",
                )
            )
            written += 1
            continue
        currency = spend["currency"]
        same_currency = [o for o in channel_orders if o.currency == currency]
        if not same_currency:
            verdict, cac = "insufficient data", None
        else:
            cac = int(spend["amount_minor"]) // len(same_currency)
            verdict = "kill" if cac > kill_line_minor(currency, cfg) else "keep"
        session.add(
            Learning(
                week_start=week, lever="channel_cac", lever_value=channel,
                kpi=f"cac_{currency.lower()}", score=cac, sample_size=len(same_currency),
                verdict=verdict,
            )
        )
        written += 1

    # Qualitative pass — the model may add 'test' suggestions; failures never sink learn.
    try:
        suggestions = llm.complete_json("learn_v1.md", {"brief": render_brief(session), "week": str(week)})
        for s in suggestions if isinstance(suggestions, list) else []:
            if isinstance(s, dict) and s.get("lever") and s.get("lever_value"):
                session.add(
                    Learning(
                        week_start=week, lever=str(s["lever"]), lever_value=str(s["lever_value"]),
                        kpi="model_suggestion", score=None, sample_size=None, verdict="test",
                    )
                )
                written += 1
    except Exception as e:
        log(f"learn: model suggestion pass skipped ({type(e).__name__})")

    session.flush()
    log(f"learn {week}: wrote {written} verdicts across {len(posts)} published posts")
    return {"week": str(week), "verdicts": written, "cold_start": False}
