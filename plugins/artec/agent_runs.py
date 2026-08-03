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


def start_run(job: str, session_id: str | None = None, engine=None) -> int | None:
    """Open an agent_runs row. Returns its id, or None if the write failed (never raises —
    observability must not be able to take down the job it is observing)."""
    try:
        eng = _eng(engine)
        with eng.begin() as conn:
            row = conn.execute(
                _json_stmt("INSERT INTO agent_runs (job, session_id, started_at, status, "
                           "tools_called) VALUES (:j, :s, :t, 'running', :tc) "
                           "RETURNING id", "tc"),
                {"j": job, "s": session_id, "t": datetime.now(UTC), "tc": []},
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
                opened = conn.execute(
                    _json_stmt("INSERT INTO agent_runs (job, session_id, started_at, status, "
                               "tools_called) VALUES (:j, :s, :t, 'running', :tc) "
                               "RETURNING id", "tc"),
                    {"j": "telegram-session", "s": str(session_id),
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


def week_to_date_spend_cents(engine=None, days: int = 7) -> int:
    """The meter the weekly agent cap reads. Sums cost_cents over the window."""
    eng = _eng(engine)
    with eng.begin() as conn:
        total = conn.execute(text(
            "SELECT COALESCE(SUM(cost_cents), 0) FROM agent_runs "
            "WHERE started_at >= :since"),
            {"since": datetime.now(UTC).replace(microsecond=0)
             - __import__("datetime").timedelta(days=days)}).scalar()
    return int(total or 0)
