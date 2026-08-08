"""The agent spend meter was pinned at zero, and hermes had been measuring all along.

`agent_runs.cost_cents` is summed by `week_to_date_spend_cents`, which feeds the A6.2 weekly
agent cap and the digest's "agent - week to date" line. Nothing in production ever wrote that
column: `start_run`/`finish_run` have no call site outside tests, because the three brain jobs
are native `hermes cron` entries whose entire payload is a prompt — there is no Python wrapper
around a cron firing to call them, and the plugin's only hook is `pre_tool_call`. So every row
in production was opened by `record_tool_call_for_session` as
`job='telegram-session' / trigger='manual' / status='running'`, and the meter read $0.00
forever: a measured-looking zero for a quantity nothing measured.

THE MEASUREMENT ALREADY EXISTS. hermes-agent's own `sessions` table carries, per session:

    source, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    estimated_cost_usd, actual_cost_usd, cost_status, cost_source, api_call_count,
    started_at, ended_at, end_reason, model

So the meter does not need inventing. It needs READING, and the reading has to cross from the
brain's volume into Postgres, which is what this script does.

THE JOIN IS DIRECT, NOT INFERRED. A cron session id is `cron_<jobid>_<YYYYMMDD>_<HHMMSS>` and
`hermes cron list` prints that same `<jobid>` beside the job's name:

    22cf30408fee [active]
      Name:      hermes-trading-daily-review

WHAT SUPPLIES EACH SIDE: the cron registry supplies jobid -> name; the message store supplies
jobid -> cost. Neither is derived from the other, and a session whose job id is not in the
listing is reported as such rather than guessed at.

STALE IS NOT ZERO, and the store says so itself: `cost_status` is 'estimated', 'unknown', or
NULL, and one real row carries `estimated_cost_usd = 0.0` with `cost_status = 'unknown'`. A
run whose cost is unknown is written with cost_cents NULL, never 0 — writing 0 would make an
unmeasured week indistinguishable from a free one, which is the exact defect being fixed.

SELF-CONTAINED: the brain image carries no `app` package (enforced by
tests/unit/test_plugin_is_self_contained.py).
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# hermes writes epoch seconds as REAL. A session that never ended has ended_at NULL.
COST_IS_KNOWN = ("estimated", "actual")

# USD PER MILLION TOKENS. The store records tokens for every session but prices only some:
# the brain runs claude-opus-5 through the Anthropic transport, and hermes' pricing snapshot
# did not cover it, so the first production run reported "5 cron session(s); 0 with a known
# cost". Tokens without a price is not the same as no data — we know the rates, so the meter
# prices what the store cannot.
#
# A MODEL ABSENT FROM THIS TABLE STAYS UNMEASURED, never guessed at a neighbouring model's
# rate: a wrong number is worse than an honest gap, because a wrong number gets spent against.
# Cache read is 0.1x input and cache write 1.25x input (5-minute TTL) per Anthropic pricing.
PRICING_USD_PER_MTOK = {
    "claude-opus-5":     {"in": 5.00, "out": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-8":   {"in": 5.00, "out": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5":   {"in": 3.00, "out": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5":  {"in": 1.00, "out":  5.00, "cache_read": 0.10, "cache_write": 1.25},
}


def price_from_tokens(model: str | None, row) -> int | None:
    """Cents from token counts, or None when this model has no rate in the table.

    The four token classes are ADDITIVE, matching the Anthropic usage contract: `input_tokens`
    is the uncached remainder, with cache reads and cache writes reported separately. Summing
    them the other way (treating input as inclusive) would double-count the cached prefix,
    which on this workload is the largest term by an order of magnitude.
    """
    rates = PRICING_USD_PER_MTOK.get((model or "").strip())
    if not rates:
        return None
    usd = (
        (row["input_tokens"] or 0) * rates["in"]
        + (row["output_tokens"] or 0) * rates["out"]
        + (row["cache_read_tokens"] or 0) * rates["cache_read"]
        + (row["cache_write_tokens"] or 0) * rates["cache_write"]
    ) / 1_000_000
    return int(round(usd * 100))


def cron_job_names() -> dict[str, str]:
    """{job_id: name} from `hermes cron list`. Empty means unknown, and unknown never guesses.

    The listing is the same artefact the boot guard verifies by, for the same reason:
    `hermes cron create` exits 0 on failure, so the listing is the only honest registry.
    """
    try:
        out = subprocess.run(["hermes", "cron", "list"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120).stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    names: dict[str, str] = {}
    current = None
    for line in out.splitlines():
        header = re.match(r"^\s*([0-9a-f]{6,})\s+\[", line)
        if header:
            current = header.group(1)
            continue
        named = re.match(r"^\s*Name:\s*(\S+)", line)
        if named and current:
            names[current] = named.group(1)
            current = None
    return names


def _epoch(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def read_cron_sessions(home: Path) -> list[dict]:
    """Every cron session in the ARTEC profile store, with its real cost."""
    profile = ""
    try:
        profile = (home / "active_profile").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    db = home / "profiles" / profile / "state.db" if profile else home / "state.db"
    if not db.is_file():
        return []

    names = cron_job_names()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, source, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, estimated_cost_usd, actual_cost_usd, cost_status, "
            "api_call_count, started_at, ended_at, end_reason "
            "FROM sessions WHERE source = 'cron'").fetchall()
    except sqlite3.Error as e:
        print(f"report_agent_runs: cannot read sessions: {e}", file=sys.stderr)
        return []
    finally:
        conn.close()

    out = []
    for row in rows:
        session_id = str(row["id"])
        job_id = session_id.split("_")[1] if session_id.count("_") >= 2 else ""
        # A job id absent from the listing is REPORTED, not guessed. A cron job deleted after
        # it ran still has sessions, and labelling those with a neighbouring name would be
        # worse than saying which id it was.
        job = names.get(job_id) or f"cron:{job_id or 'unknown'}"

        status = row["end_reason"] or ("running" if row["ended_at"] is None else "unknown")
        if status == "cron_complete":
            status = "ok"

        # COST: only when the store says it knows. `estimated_cost_usd` is 0.0 on rows whose
        # `cost_status` is 'unknown' — summing those as zero is how an unmeasured week reads
        # as a free one.
        usd = row["actual_cost_usd"]
        if usd is None and (row["cost_status"] in COST_IS_KNOWN):
            usd = row["estimated_cost_usd"]
        cost_cents = None if usd is None else int(round(float(usd) * 100))
        cost_source = "hermes" if cost_cents is not None else None

        # THE STORE PRICES SOME MODELS AND NOT OTHERS. It records tokens for all of them, so
        # where hermes does not know the rate and we do, price it here rather than recording
        # an honest-but-useless NULL. Still NULL for a model absent from the rate table.
        if cost_cents is None:
            cost_cents = price_from_tokens(row["model"], row)
            cost_source = "artec-rate-table" if cost_cents is not None else None

        tokens = (row["input_tokens"] or 0) + (row["output_tokens"] or 0)
        out.append({
            "session_id": session_id, "job": job, "trigger": "cron", "status": status,
            "started_at": _epoch(row["started_at"]), "finished_at": _epoch(row["ended_at"]),
            "tokens": tokens or None, "cost_cents": cost_cents,
            "cost_status": row["cost_status"] or "unknown", "model": row["model"],
            "cost_source": cost_source,
        })
    return out


def _engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True)


def sync(rows: list[dict], engine) -> dict:
    """UPSERT by session_id. UPDATE rather than INSERT where a row already exists, because
    `record_tool_call_for_session` will already have opened one for this session — labelled
    `telegram-session`/`manual`/`running`. Those rows are REPAIRED here rather than
    duplicated: a cron firing and an operator chat must not look alike, which is the whole
    point of migration 0009's `trigger` column."""
    from sqlalchemy import text

    written, repaired, unmeasured = 0, 0, 0
    with engine.begin() as conn:
        for row in rows:
            if row["cost_cents"] is None:
                unmeasured += 1
            existing = conn.execute(
                text("SELECT id FROM agent_runs WHERE session_id = :s ORDER BY id LIMIT 1"),
                {"s": row["session_id"]}).first()
            params = {k: row[k] for k in ("job", "trigger", "status", "started_at",
                                          "finished_at", "tokens", "cost_cents")}
            params["s"] = row["session_id"]
            if existing:
                conn.execute(text(
                    "UPDATE agent_runs SET job = :job, trigger = :trigger, status = :status, "
                    "started_at = :started_at, finished_at = :finished_at, tokens = :tokens, "
                    "cost_cents = :cost_cents WHERE id = :i"),
                    {**params, "i": int(existing[0])})
                repaired += 1
            else:
                conn.execute(text(
                    "INSERT INTO agent_runs (job, session_id, started_at, finished_at, "
                    "status, tokens, cost_cents, trigger) VALUES (:job, :s, :started_at, "
                    ":finished_at, :status, :tokens, :cost_cents, :trigger)"), params)
                written += 1
    return {"inserted": written, "repaired": repaired, "unmeasured": unmeasured,
            "total": len(rows)}


def main() -> int:
    home = os.environ.get("HERMES_HOME", "")
    if not home or not Path(home).is_dir():
        print("report_agent_runs: HERMES_HOME unset — nothing to report")
        return 0
    rows = read_cron_sessions(Path(home))
    if not rows:
        print("report_agent_runs: no cron sessions in the store yet")
        return 0

    measured = [r for r in rows if r["cost_cents"] is not None]
    total_cents = sum(r["cost_cents"] for r in measured)
    print(f"report_agent_runs: {len(rows)} cron session(s); {len(measured)} with a known "
          f"cost totalling ${total_cents / 100:.2f}; "
          f"{len(rows) - len(measured)} UNMEASURED (recorded NULL, never 0)")
    for r in sorted(rows, key=lambda r: r["started_at"] or datetime.min.replace(tzinfo=UTC)):
        amount = "unmeasured" if r["cost_cents"] is None else f"${r['cost_cents'] / 100:.2f}"
        print(f"  {r['job']:<22} {r['status']:<10} {amount:>10}  {r['session_id']}")

    engine = _engine()
    if engine is None:
        print("report_agent_runs: DATABASE_URL unset — not recorded", file=sys.stderr)
        return 0
    try:
        print(f"report_agent_runs: {sync(rows, engine)}")
    except Exception as e:                                    # noqa: BLE001
        print(f"report_agent_runs: could not record: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
