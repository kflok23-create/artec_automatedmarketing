"""POSTGRES-ONLY behaviours. Every test here is `pg`-marked and SKIPS without a real
Postgres — it never silently falls back to SQLite.

Why this file exists: the v4 post_id allocator, advisory locks, jsonb payloads and
sequence semantics have no SQLite equivalent. Testing them on SQLite would verify a
different implementation from the one that runs in production — the packaging/environment
class that has already put defects into this production. A skipped test is honest; a
SQLite stand-in for a Postgres guarantee is not.

CI runs these against the same throwaway Postgres it stands up for `alembic upgrade head`.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.locks import NotPostgres, job_lock, lock_key, try_acquire
from app.models import AgentRun, Digest, Metric, Post
from app.post_ids import allocate_post_id, ensure_allocator, next_post_number

pytestmark = pytest.mark.pg

WEEK = date(2026, 8, 24)


# ---- H · advisory locks: two REAL connections, not a simulation --------------------------

def test_second_replica_no_ops_while_the_first_holds_the_job(pg_engine):
    job = "daily-publish-by-slot"
    with pg_engine.connect() as replica_a, pg_engine.connect() as replica_b:
        assert try_acquire(replica_a, job) is True     # first scheduler wins
        assert try_acquire(replica_b, job) is False    # second must no-op, not double-fire


def test_lock_is_released_when_the_holder_disconnects(pg_engine):
    job = "daily-measure-0630"
    conn_a = pg_engine.connect()
    assert try_acquire(conn_a, job) is True
    conn_a.close()                                     # a crashed replica
    with pg_engine.connect() as conn_b:
        # A dead holder must not leave the job permanently unrunnable.
        assert try_acquire(conn_b, job) is True


def test_job_lock_context_manager_releases(pg_engine):
    job = "weekly-report-snapshot"
    with pg_engine.connect() as conn:
        with job_lock(conn, job) as owned:
            assert owned is True
        with pg_engine.connect() as other:
            assert try_acquire(other, job) is True     # released on exit


def test_distinct_jobs_do_not_block_each_other(pg_engine):
    with pg_engine.connect() as a, pg_engine.connect() as b:
        assert try_acquire(a, "job-one") is True
        assert try_acquire(b, "job-two") is True


def test_lock_keys_are_stable_and_distinct():
    assert lock_key("job-one") == lock_key("job-one")
    assert lock_key("job-one") != lock_key("job-two")
    assert 0 < lock_key("job-one") < 2**63


def test_advisory_lock_refuses_on_non_postgres(engine):
    # The guarantee is Postgres-only; on SQLite it must RAISE rather than pretend.
    with engine.connect() as conn, pytest.raises(NotPostgres):
        try_acquire(conn, "any-job")


# ---- §0 · the post_id SEQUENCE — the production allocator, finally executed ---------------

def test_sequence_allocates_monotonically_on_postgres(pg_engine):
    with pg_engine.begin() as conn:
        ensure_allocator(conn)
        first = next_post_number(conn)
        second = next_post_number(conn)
    assert second == first + 1


def test_sequence_never_reissues_an_id_already_in_use(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as s:
        s.add(Post(post_id="post_9500", week_start=WEEK, channel="instagram",
                   status="DRAFT", slot="evening"))
        s.commit()
    with pg_engine.begin() as conn:
        conn.execute(text("DROP SEQUENCE IF EXISTS post_id_seq"))
        ensure_allocator(conn)                 # seeded from max(suffix)+1
        assert next_post_number(conn) > 9500


def test_nextval_survives_a_rolled_back_transaction(pg_engine):
    """The reason production uses a SEQUENCE rather than a counter row: nextval is
    non-transactional, so a rollback cannot hand the same id out twice."""
    with pg_engine.begin() as conn:
        ensure_allocator(conn)
    conn = pg_engine.connect()
    trans = conn.begin()
    taken = next_post_number(conn)
    trans.rollback()
    conn.close()
    with pg_engine.begin() as conn2:
        assert next_post_number(conn2) > taken   # the rolled-back id is NOT reused


def test_concurrent_allocation_never_collides(pg_engine):
    with pg_engine.begin() as conn:
        ensure_allocator(conn)
    ids = []
    conns = [pg_engine.connect() for _ in range(5)]
    try:
        for _ in range(4):
            for c in conns:
                ids.append(allocate_post_id(c))
    finally:
        for c in conns:
            c.close()
    assert len(ids) == len(set(ids)), "concurrent allocation produced a duplicate post_id"


# ---- C · jsonb round-trips as structure, not text ----------------------------------------

def test_digest_payload_round_trips_as_jsonb(pg_engine):
    payload = {"needs_you": [{"kind": "video_review", "post_id": "post_1"}],
               "numbers": {"revenue": {"SGD": 2}, "engagement": {"impressions": 10}}}
    with Session(pg_engine, expire_on_commit=False) as s:
        s.add(Digest(digest_date=date(2026, 8, 24), payload=payload))
        s.commit()
    with Session(pg_engine) as s:
        row = s.query(Digest).one()
        # SQLite would happily store this as TEXT and still pass a naive assertion.
        assert isinstance(row.payload, dict)
        assert row.payload["needs_you"][0]["post_id"] == "post_1"
    with pg_engine.connect() as conn:
        # Queried as structure by the database itself — proof it is real jsonb.
        got = conn.execute(text(
            "SELECT payload -> 'numbers' -> 'revenue' ->> 'SGD' FROM digests")).scalar()
        assert got == "2"


# ---- G · agent_runs under concurrent writers ----------------------------------------------

def test_agent_runs_accepts_concurrent_inserts_from_two_writers(pg_engine):
    from plugins.artec import agent_runs as ar

    a = ar.start_run("learn-ideate", session_id="s-a", engine=pg_engine)
    b = ar.start_run("weekly-gate", session_id="s-b", engine=pg_engine)
    assert a is not None and b is not None and a != b
    ar.record_tool_call(a, "read_brief", engine=pg_engine)
    ar.record_tool_call(a, "write_plan", engine=pg_engine)
    ar.finish_run(a, status="ok", tokens=1200, cost_cents=9, engine=pg_engine)
    ar.finish_run(b, status="ok", tokens=800, cost_cents=6, engine=pg_engine)

    with Session(pg_engine) as s:
        rows = {r.id: r for r in s.query(AgentRun).all()}
        assert len(rows) == 2
        run_a = rows[a]
        assert [c["tool"] for c in run_a.tools_called] == ["read_brief", "write_plan"]
        assert run_a.cost_cents == 9 and run_a.finished_at is not None
    assert ar.week_to_date_spend_cents(engine=pg_engine) == 15


# ---- metrics NULL-vs-zero under a real upsert ---------------------------------------------

def test_omitted_metric_stays_null_on_postgres(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as s:
        s.add(Post(post_id="post_9600", week_start=WEEK, channel="tiktok",
                   status="PUBLISHED", slot="evening"))
        s.add(Metric(post_id="post_9600", channel="tiktok", metric_date=WEEK,
                     impressions=1200, source="manual", collected_at=datetime.now(UTC)))
        s.commit()
    with pg_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT impressions, clicks, saves FROM metrics WHERE post_id = 'post_9600'")).first()
    assert row[0] == 1200
    assert row[1] is None and row[2] is None, "unmeasured must be NULL, never 0"
