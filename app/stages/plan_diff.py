"""`artec plan-diff --week` — the shadow-mode artefact the operator reads every Sunday.

The OVERLAP section is the judgement surface: slots where BOTH planners chose something,
shown field by field with disagreements marked, plus learning cross-references — where a
planner's choice matches a keep/test verdict from `learnings`, it is flagged, so "the
agent visibly used a learning the bespoke planner ignored" is readable at a glance.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Learning, PlanShadow, Post

COMPARE_FIELDS = ("channel", "angle", "hook", "cta_type", "slot")

# Fields that double as learning levers (channel pairs are matched on channel already).
LEVER_FIELDS = ("angle", "hook", "cta_type", "slot")


def _rowdict(obj) -> dict:
    return {f: getattr(obj, f) for f in COMPARE_FIELDS}


def _learning_index(session: Session) -> dict[tuple[str, str], str]:
    """(lever, lever_value) → strongest recent verdict (keep beats test)."""
    index: dict[tuple[str, str], str] = {}
    rows = session.execute(
        select(Learning)
        .where(Learning.verdict.in_(["keep", "test", "kill"]))
        .order_by(Learning.week_start.desc(), Learning.id.desc())
        .limit(200)
    ).scalars()
    for row in rows:
        key = (row.lever or "", str(row.lever_value or ""))
        if key not in index:  # newest first — first wins
            index[key] = row.verdict or ""
    return index


def _verdict_for(index: dict, field: str, value) -> str | None:
    if not value:
        return None
    return index.get((field, str(value)))


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
    learnings = _learning_index(session)

    b_index = {(r["channel"], r["slot"]): r for r in bespoke}
    a_index = {(r["channel"], r["slot"]): r for r in agent}
    paired_keys = sorted(set(b_index) & set(a_index))

    # A8 — A ONE-SIDED COMPARISON IS NOT A COMPARISON.
    #
    # `agreement[field] = 0.0` when there is nothing to compare is the same defect as
    # `learn` scoring an unmeasured week at zero: an ABSENCE reported as a VALUE. 0.0 reads
    # as "the planners disagreed about everything"; the truth is that one of them did not
    # speak. None is the only honest answer, and it is what `learn` now returns for a lever
    # it cannot score.
    #
    # This matters on 2026-08-09 specifically. Job 3 — the agent's LEARN→IDEATE at 07:00 —
    # has NEVER completed a session in production, so an empty `plans_shadow` is the LIKELY
    # case at 08:00, not the edge case. Job 2 shipped without a dispatch and plan-diff would
    # have compared one plan against nothing and reported agreement with itself; this is the
    # same failure arriving from the other side.
    one_sided = (not agent) or (not bespoke)
    agreement: dict[str, float | None] = {}
    for field in COMPARE_FIELDS:
        if not paired_keys:
            agreement[field] = None
            continue
        hits = sum(1 for k in paired_keys
                   if (b_index[k][field] or "") == (a_index[k][field] or ""))
        agreement[field] = round(hits / len(paired_keys), 2)

    pairs = []
    for k in paired_keys:
        fields = []
        for field in COMPARE_FIELDS:
            b_val, a_val = b_index[k][field], a_index[k][field]
            entry: dict = {"field": field, "bespoke": b_val, "agent": a_val,
                           "agree": (b_val or "") == (a_val or "")}
            if field in LEVER_FIELDS:
                b_verdict = _verdict_for(learnings, field, b_val)
                a_verdict = _verdict_for(learnings, field, a_val)
                if a_verdict:
                    entry["agent_learning"] = a_verdict
                if b_verdict:
                    entry["bespoke_learning"] = b_verdict
                # The line the operator judges by: the agent leaned on a keep-verdict the
                # bespoke planner ignored (or walked into a kill).
                if not entry["agree"]:
                    if a_verdict == "keep" and b_verdict != "keep":
                        entry["note"] = f"agent applied learning: {field}={a_val!r} is a KEEP lever"
                    elif b_verdict == "keep" and a_verdict != "keep":
                        entry["note"] = f"bespoke held the KEEP lever {field}={b_val!r}; agent deviated"
                    elif a_verdict == "kill":
                        entry["note"] = f"agent chose a KILL lever: {field}={a_val!r}"
            fields.append(entry)
        pairs.append({"key": list(k), "fields": fields,
                      "disagreements": sum(1 for f in fields if not f["agree"])})

    return {
        "week": str(week_start),
        "pairs": pairs,
        "agreement": agreement,
        # Named, not inferred from an empty list by whoever reads this next.
        "one_sided": one_sided,
        "bespoke_count": len(bespoke),
        "agent_count": len(agent),
        "unique_bespoke": [b_index[k] for k in sorted(set(b_index) - set(a_index))],
        "unique_agent": [a_index[k] for k in sorted(set(a_index) - set(b_index))],
    }


def print_diff(diff: dict, log=print) -> None:
    log(f"PLAN DIFF — week {diff['week']}")
    log(f"  bespoke: {diff.get('bespoke_count', '?')} plan(s) · "
        f"agent: {diff.get('agent_count', '?')} plan(s)")
    if not diff["pairs"] and not diff["unique_bespoke"] and not diff["unique_agent"]:
        log("  no plans on either side for this week")
        return

    # THE LOUD FAILURE. Previously this fell through to "the planners filled disjoint
    # slots", which is FALSE when one planner produced nothing — a one-sided comparison
    # dressed as a two-sided result, which is exactly what A8 exists to prevent.
    if diff.get("one_sided"):
        missing = "agent" if not diff.get("agent_count") else "bespoke"
        present = "bespoke" if missing == "agent" else "agent"
        log("")
        log(f"  *** ONE-SIDED — the {missing.upper()} planner produced NOTHING for this "
            f"week. There is no comparison to make: every 'difference' below is just the "
            f"{present} plan, and agreement is UNDEFINED, not zero.")
        if missing == "agent":
            log("  *** Job 3 (agent LEARN→IDEATE, SUN 07:00) did not write plans_shadow. "
                "Check its agent_runs row before reading anything below as a judgement.")
        log("  *** Do not treat this as the planners disagreeing.")

    if diff["pairs"]:
        log(f"\n== OVERLAP — {len(diff['pairs'])} slot(s) both planners filled (the Sunday judgement surface) ==")
        for pair in diff["pairs"]:
            ch, slot = pair["key"]
            log(f"\n  {ch} @ {slot} — {pair['disagreements']} field(s) differ")
            for f in pair["fields"]:
                marker = "=" if f["agree"] else "≠"
                tags = []
                if f.get("agent_learning"):
                    tags.append(f"agent:{f['agent_learning']}")
                if f.get("bespoke_learning"):
                    tags.append(f"bespoke:{f['bespoke_learning']}")
                tag_str = f"  [{' '.join(tags)}]" if tags else ""
                log(f"    {f['field']:<10} {marker}  bespoke: {str(f['bespoke'])[:44]:<46} "
                    f"agent: {str(f['agent'])[:44]}{tag_str}")
                if f.get("note"):
                    log(f"               ↳ {f['note']}")
    elif not diff.get("one_sided"):
        # "Disjoint slots" is a claim about TWO planners that both spoke. Printing it when
        # one produced nothing was the false line: a one-sided result dressed as a
        # two-sided one. The ONE-SIDED banner above already said what happened.
        log("\n== OVERLAP — none: the planners filled disjoint slots ==")

    log("\n  agreement rate per field (paired slots only):")
    for field, rate in diff["agreement"].items():
        # None means NOTHING WAS COMPARED — it must not render as a percentage, because
        # "0%" and "undefined" are the two readings this whole change exists to separate.
        log(f"    {field:<10} {'—  (nothing to compare)' if rate is None else f'{rate:.0%}'}")
    if diff["unique_bespoke"]:
        log("\n  only bespoke planned:")
        for r in diff["unique_bespoke"]:
            log(f"    {r['channel']} @ {r['slot']}: {r['hook']}")
    if diff["unique_agent"]:
        log("\n  only the agent planned:")
        for r in diff["unique_agent"]:
            log(f"    {r['channel']} @ {r['slot']}: {r['hook']}")
