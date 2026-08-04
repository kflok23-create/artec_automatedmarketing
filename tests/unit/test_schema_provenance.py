"""THE GUARD WHOSE ABSENCE CAUSED THE DETOUR.

The first full run of the suite against real Postgres produced six failures, all
`relation "post_id_seq" does not exist`. The cause was not application code. The test
fixture built its schema with `Base.metadata.create_all()`, which creates TABLES from
model metadata; `post_id_seq` is created by migration 0004. Production's schema comes from
`alembic upgrade head`, so the fixture was testing a schema that never ships.

Nothing in the suite could have caught that, because nothing asserted where the schema came
from. Every test took the fixture's word for it. That is the standing review question
applied to the fixture: the thing under test supplied one side of the comparison.

ASYMMETRY, STATED RATHER THAN FAKED. On Postgres this asserts schema PROVENANCE — that the
schema was built by migrations and carries a migration-only object. On SQLite it asserts
only that the allocator is usable, because on SQLite the schema deliberately does NOT come
from migrations (DECISIONS.md #7: unit tests run on in-memory SQLite built by create_all).
Writing a symmetric-looking assert that means something weaker on one substrate would be
the same failure this file exists to prevent.
"""

from __future__ import annotations

import os

from sqlalchemy import text

from app.post_ids import POST_ID_START, SEQ_NAME, allocate_post_id

ON_POSTGRES = os.environ.get("ARTEC_TEST_SUBSTRATE") == "postgres"


def test_the_allocator_exists_after_fixture_setup_without_being_asked(engine):
    """`post_id_seq` must be present from schema setup alone. On Postgres nothing in the
    allocation path creates it — `next_post_number` goes straight to `nextval` — so if
    setup did not supply it, the first injection at the gate raises UndefinedTable."""
    with engine.connect() as conn:
        if ON_POSTGRES:
            kind = conn.execute(text(
                "SELECT relkind FROM pg_class WHERE relname = :n"), {"n": SEQ_NAME}).scalar()
            assert kind == "S", (
                f"{SEQ_NAME} is not a SEQUENCE on Postgres (relkind={kind!r}). "
                "create_all() does not create it; migration 0004 does. If this fails, the "
                "fixture stopped building the schema the way production does.")
        else:
            # SQLite has no sequences; the allocator is a single-row table created lazily
            # by ensure_allocator(). Assert it is REACHABLE, not that setup produced it.
            assert allocate_post_id(conn).startswith("post_")


def test_postgres_schema_was_built_by_migrations_not_by_metadata(engine):
    """Provenance, Postgres only. `alembic_version` is written by `alembic upgrade`; it is
    not in `Base.metadata`, so `create_all()` can never produce it. Its presence is proof
    of which path built this schema — the fact the six failures turned on."""
    if not ON_POSTGRES:
        return
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert rev, "alembic_version is empty — the schema did not come from migrations"


def test_allocated_ids_start_at_or_above_the_floor_and_advance(engine):
    """Both substrates. Guards the seeding rule in `starting_value()`: never hand out an id
    at or below one already in use, and never hand the same id out twice."""
    with engine.begin() as conn:
        first = int(allocate_post_id(conn).removeprefix("post_"))
        second = int(allocate_post_id(conn).removeprefix("post_"))
    assert first >= POST_ID_START
    assert second == first + 1
