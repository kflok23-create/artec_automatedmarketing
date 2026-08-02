"""REPORT — two blocks that are NEVER combined: REVENUE (orders) and ENGAGEMENT
(events + metrics). Plus unattributed revenue, unmeasured posts, and PARKED posts.

The build_report return value keeps the lanes in separate dicts; no code path sums an
orders figure with a metrics figure (test-asserted).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import all_config, net_cm_minor
from app.models import Event, Metric, Order, Post
from app.stages.learn import last_week_start


def build_report(session: Session, week_start: date | None = None) -> dict:
    week = week_start or last_week_start()
    cfg = all_config(session)
    posts = list(session.execute(select(Post).where(Post.week_start == week)).scalars())
    post_ids = [p.post_id for p in posts]

    # ---- REVENUE LANE (orders only) ---------------------------------------------------
    revenue: dict = {"by_currency": {}, "net_cm_by_post": defaultdict(lambda: {"orders": 0, "net_cm_minor": 0}),
                     "unattributed": {"orders": 0, "amount_minor_by_currency": {}}}
    for o in session.execute(select(Order)).scalars():
        if o.post_id and o.post_id in post_ids:
            cur = o.currency or "?"
            bucket = revenue["by_currency"].setdefault(cur, {"orders": 0, "net_cm_minor": 0})
            bucket["orders"] += 1
            try:
                bucket["net_cm_minor"] += net_cm_minor(cur, cfg)
                revenue["net_cm_by_post"][o.post_id]["net_cm_minor"] += net_cm_minor(cur, cfg)
            except ValueError:
                pass
            revenue["net_cm_by_post"][o.post_id]["orders"] += 1
        elif o.post_id is None:
            revenue["unattributed"]["orders"] += 1
            cur = o.currency or "?"
            amounts = revenue["unattributed"]["amount_minor_by_currency"]
            amounts[cur] = amounts.get(cur, 0) + (o.amount_minor or 0)
    revenue["net_cm_by_post"] = dict(revenue["net_cm_by_post"])

    # ---- ENGAGEMENT LANE (events + metrics only) --------------------------------------
    engagement: dict = {"metrics_by_post": {}, "events_by_post": {}}
    measured_posts: set[str] = set()
    for m in session.execute(select(Metric).where(Metric.post_id.in_(post_ids or [""]))).scalars():
        measured = {f: getattr(m, f) for f in
                    ("impressions", "completion_rate", "watch_time_s", "saves", "shares", "clicks")
                    if getattr(m, f) is not None}
        if measured:
            measured_posts.add(m.post_id)
            agg = engagement["metrics_by_post"].setdefault(m.post_id, {})
            for k, v in measured.items():
                agg[k] = (agg.get(k) or 0) + float(v)
    for e in session.execute(select(Event).where(Event.post_id.in_(post_ids or [""]))).scalars():
        engagement["events_by_post"][e.post_id] = engagement["events_by_post"].get(e.post_id, 0) + 1

    unmeasured = [p.post_id for p in posts if p.status == "PUBLISHED" and p.post_id not in measured_posts]
    parked = [
        {"post_id": p.post_id, "wishlist": p.asset_wishlist or [], "reason": p.park_reason}
        for p in posts if p.status == "PARKED"
    ]
    return {"week": str(week), "revenue": revenue, "engagement": engagement,
            "unmeasured": unmeasured, "parked": parked}


def print_report(report: dict, log=print) -> None:
    log(f"WEEK {report['week']}")
    log("")
    log("== REVENUE (orders only) ==")
    for cur, b in sorted(report["revenue"]["by_currency"].items()):
        log(f"  {cur}: {b['orders']} orders, net CM {b['net_cm_minor']} minor units")
    for pid, b in sorted(report["revenue"]["net_cm_by_post"].items()):
        log(f"  {pid}: {b['orders']} orders, net CM {b['net_cm_minor']}")
    ua = report["revenue"]["unattributed"]
    log(f"  UNATTRIBUTED: {ua['orders']} orders {ua['amount_minor_by_currency']}")
    log("")
    log("== ENGAGEMENT (events + metrics only) ==")
    for pid, agg in sorted(report["engagement"]["metrics_by_post"].items()):
        log(f"  {pid}: " + ", ".join(f"{k}={v}" for k, v in agg.items()))
    for pid, n in sorted(report["engagement"]["events_by_post"].items()):
        log(f"  {pid}: {n} site events")
    log("")
    if report["unmeasured"]:
        log("unmeasured (NULL, not zero): " + ", ".join(report["unmeasured"]))
    if report["parked"]:
        log("parked awaiting assets:")
        for p in report["parked"]:
            for w in p["wishlist"]:
                log(f"  {p['post_id']} → {w.get('target_folder')}: {w.get('description')}")
