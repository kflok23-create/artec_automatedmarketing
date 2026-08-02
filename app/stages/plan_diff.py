"""`artec plan-diff --week` — the shadow-mode artefact. Bespoke and agent plans side by
side with a per-field agreement rate and the ideas unique to each. The operator reads this
for two to three Sundays before flipping `plan_source`.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PlanShadow, Post

COMPARE_FIELDS = ("channel", "angle", "hook", "cta_type", "slot")


def _rowdict(obj) -> dict:
    return {f: getattr(obj, f) for f in COMPARE_FIELDS}


def build_diff(session: Session, week_start: date) -> dict:
    bespoke = [
        _rowdict(p) for p in session.execute(
            select(Post).where(Post.week_start == week_start,
                               Post.status != "REJECTED")
        ).scalars()
        if (p.plan_source or "bespoke") == "bespoke"
    ]
    agent = [
        _rowdict(r) for r in session.execute(
            select(PlanShadow).where(PlanShadow.week_start == week_start,
                                     PlanShadow.source == "agent")
        ).scalars()
    ]

    # Pair on (channel, slot) — the natural planning key.
    b_index = {(r["channel"], r["slot"]): r for r in bespoke}
    a_index = {(r["channel"], r["slot"]): r for r in agent}
    paired_keys = sorted(set(b_index) & set(a_index))

    agreement: dict[str, float] = {}
    for field in COMPARE_FIELDS:
        if not paired_keys:
            agreement[field] = 0.0
            continue
        hits = sum(1 for k in paired_keys
                   if (b_index[k][field] or "") == (a_index[k][field] or ""))
        agreement[field] = round(hits / len(paired_keys), 2)

    return {
        "week": str(week_start),
        "pairs": [{"key": list(k), "bespoke": b_index[k], "agent": a_index[k]}
                  for k in paired_keys],
        "agreement": agreement,
        "unique_bespoke": [b_index[k] for k in sorted(set(b_index) - set(a_index))],
        "unique_agent": [a_index[k] for k in sorted(set(a_index) - set(b_index))],
    }


def print_diff(diff: dict, log=print) -> None:
    log(f"PLAN DIFF — week {diff['week']}")
    if not diff["pairs"] and not diff["unique_bespoke"] and not diff["unique_agent"]:
        log("  no plans on either side for this week")
        return
    for pair in diff["pairs"]:
        ch, slot = pair["key"]
        log(f"\n  {ch} @ {slot}")
        for field in COMPARE_FIELDS:
            b, a = pair["bespoke"][field], pair["agent"][field]
            marker = "=" if (b or "") == (a or "") else "≠"
            log(f"    {field:<14} {marker}  bespoke: {b!r:<40} agent: {a!r}")
    log("\n  agreement rate per field:")
    for field, rate in diff["agreement"].items():
        log(f"    {field:<14} {rate:.0%}")
    if diff["unique_bespoke"]:
        log("\n  only bespoke planned:")
        for r in diff["unique_bespoke"]:
            log(f"    {r['channel']} @ {r['slot']}: {r['hook']}")
    if diff["unique_agent"]:
        log("\n  only the agent planned:")
        for r in diff["unique_agent"]:
            log(f"    {r['channel']} @ {r['slot']}: {r['hook']}")
