"""v4 §7·A3 — `agent_runs` was schema'd in migration 0002 and never written by any code
path. Zero INSERTs existed. This is the observability the Sunday brain has none of, and the
meter the spend cap (A6) and the digest's SPEND & HEALTH block both read from, so it lands
before either.

One row per brain job, updated as the job progresses; every tool call appends to
`tools_called`. Self-contained (textual SQL only), same as the rest of the seam.

Failure class: config/credential silence — if DATABASE_URL is wrong the writes fail
silently and the meter reads zero, which would look like "no spend" rather than "no data".
Every write is therefore best-effort but LOGGED, and the digest flags a job that produced
no agent_runs row.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.types import JSON as SAJSON

_engine = None


def _eng(engine=None):
    global _engine
    if engine is not None:
        return engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def _json_stmt(sql: str, *params: str):
    stmt = text(sql)
    if params:
        stmt = stmt.bindparams(*[bindparam(p, type_=SAJSON()) for p in params])
    return stmt


def start_run(job: str, session_id: str | None = None, engine=None,
              trigger: str = "cron") -> int | None:
    """Open an agent_runs row. Returns its id, or None if the write failed (never raises —
    observability must not be able to take down the job it is observing).

    `trigger` distinguishes a scheduled firing from a run-now mirror. It defaults to 'cron'
    because that is the path that had NO ROWS AT ALL: agent_runs held two entries, both
    operator conversations, and jobs 3, 5 and 12 had never written one. An absent row must
    mean "did not run", and it can only mean that if a run that does happen always leaves
    one — the same reasoning that makes cron registration verified by listing rather than
    by an exit code.

    A brain-side stdout line is printed alongside, matching the scheduler's
    `job N <name> due at HH:MM`, so a failure that happens before the database write is
    still visible in the deploy log.
    """
    print(f"brain: job {job} starting (trigger={trigger}, session={session_id or '-'})")
    try:
        eng = _eng(engine)
        with eng.begin() as conn:
            row = conn.execute(
                _json_stmt("INSERT INTO agent_runs (job, session_id, started_at, status, "
                           "tools_called, trigger) VALUES (:j, :s, :t, 'running', :tc, :tr) "
                           "RETURNING id", "tc"),
                {"j": job, "s": session_id, "t": datetime.now(UTC), "tc": [],
                 "tr": trigger},
            ).first()
            return int(row[0]) if row else None
    except Exception as e:
        print(f"agent_runs: could not open a row for {job}: {type(e).__name__}: {e}")
        return None


def record_tool_call(run_id: int | None, tool: str, engine=None) -> None:
    """Append a tool name to the run's tools_called. Best-effort."""
    if run_id is None:
        return
    try:
        eng = _eng(engine)
        with eng.begin() as conn:
            current = conn.execute(text(
                "SELECT tools_called FROM agent_runs WHERE id = :i"), {"i": run_id}).scalar()
            if isinstance(current, str):
                current = json.loads(current)
            calls = list(current or [])
            calls.append({"tool": tool, "at": datetime.now(UTC).isoformat()})
            conn.execute(
                _json_stmt("UPDATE agent_runs SET tools_called = :tc WHERE id = :i", "tc"),
                {"tc": calls, "i": run_id})
    except Exception as e:
        print(f"agent_runs: could not record tool call {tool}: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------------------
# A6·2 — the weekly agent spend cap, and the ORDER things degrade in.
#
# The order is the whole point. Scouting is the cheapest thing to lose and the least
# load-bearing: a week planned without trend input is a worse week, not a broken one. The
# GATE is the single most valuable human touch in the system and the one thing that must
# never be traded for headroom — so as spend approaches the cap the gate CONVERSATION
# shortens, and the gate itself never stops running. There is deliberately no code path
# that returns a posture without a gate; a property test walks the whole spend range to
# prove it, because "never skip the gate" is not something a threshold should be trusted
# with by accident.
# ---------------------------------------------------------------------------------------

DROP_SCOUTING_AT = 0.60          # of the weekly cap
SHORTEN_GATE_AT = 0.85


def spend_posture(spent_cents: int, cap_cents: int, blind: bool = False,
                  unmeasured_runs: int = 0) -> dict:
    """What the brain may spend this week's remaining budget on. Pure and total.

    `blind` is the case a bare number cannot express: runs happened and NONE of them recorded
    a cost. The posture is then reported as UNKNOWN rather than as the comfortable one — a
    meter reading zero because it is disconnected must not license full spending, which is
    exactly what it did for the whole life of this system.
    """
    cap = max(0, int(cap_cents or 0))
    spent = max(0, int(spent_cents or 0))
    fraction = (spent / cap) if cap else 0.0
    scouting = fraction < DROP_SCOUTING_AT
    gate_style = "full" if fraction < SHORTEN_GATE_AT else "short"
    actions = []
    if blind:
        actions.append(f"agent spend is UNMEASURED for all {unmeasured_runs} run(s) this "
                       "week — the cap cannot be enforced against a meter that recorded "
                       "nothing, and 0 here means unknown, not free")
    if not scouting:
        actions.append("scouting dropped — plan from the bank and last week's learnings")
    if gate_style == "short":
        actions.append("gate conversation shortened — fewer clarifying turns per draft, "
                       "same decisions")
    return {
        "spent_cents": spent, "cap_cents": cap, "fraction": round(fraction, 3),
        # Carried so every renderer can say "unknown" instead of printing a confident $0.00.
        "measured": not blind, "unmeasured_runs": int(unmeasured_runs),
        "scouting": scouting,
        "gate_style": gate_style,
        # INVARIANT: the gate always runs. Not a threshold, not a flag — a constant.
        "gate_runs": True,
        "actions": actions,
        "degraded": bool(actions),
    }


def current_posture(cap_cents: int, engine=None, days: int = 7) -> dict:
    meter = week_to_date_spend(engine=engine, days=days)
    return spend_posture(meter["cents"], cap_cents, blind=meter["blind"],
                         unmeasured_runs=meter["unmeasured_runs"])


def job_and_trigger_for(session_id: str | None) -> tuple[str, str]:
    """(job, trigger) from the session id alone — no I/O, so it is safe on every tool call.

    hermes names a cron session `cron_<jobid>_<YYYYMMDD>_<HHMMSS>`. The job id is a real
    handle: `hermes cron list` prints it beside the name, which is how
    report_agent_runs.py later replaces `cron:<jobid>` with the actual job name. Until then
    the id is carried verbatim rather than guessed at — an unresolved id is still a fact,
    where a plausible name would be a fabrication.
    """
    sid = str(session_id or "")
    if sid.startswith("cron_"):
        parts = sid.split("_")
        return (f"cron:{parts[1]}" if len(parts) > 2 else "cron:unknown"), "cron"
    return "telegram-session", "manual"


def record_tool_call_for_session(session_id: str | None, tool: str, engine=None) -> None:
    """Append a tool call to the run for THIS agent session, opening one if the job did not
    start through `start_run` (an operator-initiated conversation has no cron wrapper).

    Wired from the pre_tool_call hook, which is the only place that sees every tool call
    and the session it belongs to. Best-effort and logged — observability must never be
    able to take down the job it observes — but a silent failure here reads as "no tool
    calls" rather than "no data", so it is logged rather than swallowed.
    """
    if not session_id:
        return
    try:
        eng = _eng(engine)
        with eng.begin() as conn:
            row = conn.execute(text(
                "SELECT id FROM agent_runs WHERE session_id = :s "
                "ORDER BY id DESC LIMIT 1"), {"s": str(session_id)}).first()
            if row is None:
                # THE SESSION ID ALREADY SAYS WHICH IT IS. This hardcoded
                # `telegram-session`/`manual` on every row, so a Sunday cron firing and an
                # operator's Tuesday chat were textually identical — and migration 0009 added
                # `trigger` precisely so they would not be. The 'cron' side of that comparison
                # was structurally unsuppliable, because nothing else ever inserted a row.
                #
                # hermes names a cron session `cron_<jobid>_<YYYYMMDD>_<HHMMSS>`, so the
                # trigger is readable here with no extra plumbing. The job NAME needs the
                # cron listing, which this hook must not shell out for on every tool call —
                # deploy/hermes-brain/report_agent_runs.py resolves it and repairs the row.
                job, trigger = job_and_trigger_for(session_id)
                opened = conn.execute(
                    _json_stmt("INSERT INTO agent_runs (job, session_id, started_at, status, "
                               "tools_called, trigger) VALUES (:j, :s, :t, 'running', :tc, "
                               ":tr) RETURNING id", "tc"),
                    {"j": job, "tr": trigger, "s": str(session_id),
                     "t": datetime.now(UTC), "tc": []}).first()
                run_id = int(opened[0]) if opened else None
            else:
                run_id = int(row[0])
        record_tool_call(run_id, tool, engine=eng)
    except Exception as e:                                   # noqa: BLE001
        print(f"agent_runs: could not record tool call {tool!r}: {type(e).__name__}: {e}")


def finish_run(run_id: int | None, status: str = "ok", tokens: int | None = None,
               cost_cents: int | None = None, engine=None) -> None:
    if run_id is None:
        return
    try:
        eng = _eng(engine)
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE agent_runs SET finished_at = :t, status = :s, tokens = :tok, "
                "cost_cents = :c WHERE id = :i"),
                {"t": datetime.now(UTC), "s": status, "tok": tokens, "c": cost_cents,
                 "i": run_id})
    except Exception as e:
        print(f"agent_runs: could not close run {run_id}: {type(e).__name__}: {e}")


def week_to_date_spend(engine=None, days: int = 7) -> dict:
    """The meter, with the thing a bare sum cannot say: HOW MUCH OF THE WEEK IT SAW.

    `SELECT COALESCE(SUM(cost_cents), 0)` returns 0 for three different weeks — one where
    nothing ran, one where everything ran free, and one where everything ran and nothing
    recorded a cost. Production was the third for the whole life of the system: nothing wrote
    `cost_cents`, so the cap's meter and the digest's "agent - week to date: $0.00" were a
    measured-looking zero for a quantity nothing measured.

    STALE IS NOT ZERO. Runs whose cost is NULL are counted SEPARATELY and never summed as
    free, so a caller can tell an idle week from a blind one.
    """
    eng = _eng(engine)
    from datetime import timedelta

    since = datetime.now(UTC).replace(microsecond=0) - timedelta(days=days)
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT COALESCE(SUM(cost_cents), 0) AS cents, "
            "       COUNT(*) FILTER (WHERE cost_cents IS NOT NULL) AS measured, "
            "       COUNT(*) FILTER (WHERE cost_cents IS NULL) AS unmeasured "
            "FROM agent_runs WHERE started_at >= :since"), {"since": since}).first()
    cents, measured, unmeasured = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))
    return {"cents": cents, "measured_runs": measured, "unmeasured_runs": unmeasured,
            "blind": unmeasured > 0 and measured == 0}


def week_to_date_spend_cents(engine=None, days: int = 7) -> int:
    """Cents only, for callers that genuinely want the number. Prefer `week_to_date_spend`:
    this cannot distinguish an idle week from an unmeasured one, which is the defect."""
    return week_to_date_spend(engine=engine, days=days)["cents"]
