"""post_id allocation — OUT of `config`, so no tool ever writes config.

v4 Stage 2a allocated post ids by incrementing a `config` row. That made
`record_gate_decision(action="inject")` a tool that writes `config`, breaching the §2
invariant every time it ran — the identical shape to the `endpoint_prices_cents` problem,
and it takes the identical remedy: move the state, never weaken the rule.

Production (Postgres) uses a real SEQUENCE: `nextval` is non-transactional, so concurrent
allocation is race-safe without a lock and ids are never reused after a rollback.
SQLite (tests) has no sequences, so a single-row table is used with an atomic
UPDATE … RETURNING. Both paths are behind `allocate_post_id`, and neither reads config.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

SEQ_NAME = "post_id_seq"
POST_ID_START = 1482
_SUFFIX = re.compile(r"(\d+)$")


def is_postgres(conn: Connection) -> bool:
    return conn.engine.dialect.name == "postgresql"


def starting_value(conn: Connection) -> int:
    """max(existing numeric suffix) + 1, or POST_ID_START — whichever is higher, so a
    migration can never hand out an id that already exists."""
    highest = POST_ID_START - 1
    try:
        for (post_id,) in conn.execute(text("SELECT post_id FROM posts")).all():
            m = _SUFFIX.search(str(post_id or ""))
            if m:
                highest = max(highest, int(m.group(1)))
    except Exception:
        pass
    return max(highest + 1, POST_ID_START)


def ensure_allocator(conn: Connection) -> int:
    """Create the sequence (Postgres) or the single-row table (SQLite). Idempotent.
    Returns the value the allocator will hand out next."""
    start = starting_value(conn)
    if is_postgres(conn):
        conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQ_NAME} START WITH {start}"))
        # If the sequence already existed but is behind (e.g. posts were created by the
        # old config counter after it was created), fast-forward it. setval never rewinds.
        conn.execute(text(
            f"SELECT setval('{SEQ_NAME}', GREATEST(last_value, :s), true) "
            f"FROM {SEQ_NAME}"), {"s": start - 1})
    else:
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {SEQ_NAME} (next_value INTEGER NOT NULL)"))
        row = conn.execute(text(f"SELECT next_value FROM {SEQ_NAME}")).first()
        if row is None:
            conn.execute(text(f"INSERT INTO {SEQ_NAME} (next_value) VALUES (:s)"),
                         {"s": start})
        elif int(row[0]) < start:
            conn.execute(text(f"UPDATE {SEQ_NAME} SET next_value = :s"), {"s": start})
    return start


def next_post_number(conn: Connection) -> int:
    """Allocate one id. Race-safe: nextval on Postgres, an atomic UPDATE on SQLite."""
    if is_postgres(conn):
        return int(conn.execute(text(f"SELECT nextval('{SEQ_NAME}')")).scalar())
    ensure_allocator(conn)
    row = conn.execute(text(
        f"UPDATE {SEQ_NAME} SET next_value = next_value + 1 RETURNING next_value - 1")).first()
    return int(row[0])


def allocate_post_id(conn: Connection, prefix: str = "post_") -> str:
    return f"{prefix}{next_post_number(conn)}"
