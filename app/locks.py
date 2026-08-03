"""v4 §7·C2 — Postgres advisory locks, one per scheduled job.

A second scheduler replica must no-op rather than double-fire a job. Advisory locks are
session-scoped and released automatically if the holder dies, which is the behaviour we
want: a crashed replica must not leave a job permanently unrunnable.

POSTGRES ONLY, deliberately. There is no SQLite equivalent, and a SQLite stand-in would be
a test of nothing — so on any non-Postgres dialect this raises rather than silently
pretending to lock. `artec doctor` asserts the deployed database is Postgres.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection


class NotPostgres(RuntimeError):
    """Advisory locks are Postgres-only; pretending otherwise would be a false guarantee."""


def lock_key(job_name: str) -> int:
    """Stable 63-bit key from the job name — pg_advisory_lock takes a bigint."""
    digest = hashlib.sha256(job_name.encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def try_acquire(conn: Connection, job_name: str) -> bool:
    """Non-blocking. True = this replica owns the job for the life of the connection."""
    if conn.engine.dialect.name != "postgresql":
        raise NotPostgres(
            f"advisory locks require PostgreSQL; connected dialect is "
            f"{conn.engine.dialect.name}. Refusing to pretend the job is locked."
        )
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                             {"k": lock_key(job_name)}).scalar())


def release(conn: Connection, job_name: str) -> bool:
    if conn.engine.dialect.name != "postgresql":
        raise NotPostgres("advisory locks require PostgreSQL")
    return bool(conn.execute(text("SELECT pg_advisory_unlock(:k)"),
                             {"k": lock_key(job_name)}).scalar())


@contextmanager
def job_lock(conn: Connection, job_name: str):
    """Yields True when this replica holds the job, False when another already does.

        with job_lock(conn, "daily-publish-by-slot") as owned:
            if not owned:
                return {"skipped": "another replica holds this job"}
            ...
    """
    owned = try_acquire(conn, job_name)
    try:
        yield owned
    finally:
        if owned:
            try:
                release(conn, job_name)
            except Exception:
                pass  # connection teardown releases it anyway
