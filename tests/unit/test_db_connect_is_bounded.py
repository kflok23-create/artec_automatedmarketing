"""A connect with no timeout is not a connect that fails — it is one that never answers.

    2026-08-08T09:05:14Z  artec-scheduler  Starting Container
    2026-08-08T09:23:52Z  (nothing)

Nineteen minutes, against a baseline where the boot banner lands 0.3 SECONDS after the
container starts. Not a crash — 66 MB resident, 0% CPU, ~0 bytes transmitted. Blocked on the
first connection inside `wait_for_schema`, with nine jobs behind it.

`wait_for_schema` catches Exception, announces the wait and polls. It could never run:
libpq's connect_timeout defaults to 0, meaning wait forever, so the call it retries never
returned and never raised. THE RETRY LOOP WAS A GUARD CONNECTED TO NOTHING.

WHAT SUPPLIES EACH SIDE, for the bound itself: the timeout is ours (DB_CONNECT_TIMEOUT_S);
the elapsed time is the kernel's. Neither is derived from the other.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app import db

# RFC 5737 TEST-NET-1. Reserved for documentation, routed nowhere: a SYN gets no answer, so
# the connect hangs rather than being refused. That is the production failure, reproduced.
BLACKHOLE = "postgresql+psycopg://u:p@192.0.2.1:5432/x"


def test_postgres_engines_carry_a_bounded_connect_timeout():
    """The part that CANNOT pass by accident. A sandbox that refuses instantly would let the
    timing test below pass without the bound ever being applied; this asserts the bound is
    actually in the kwargs the engine is built with."""
    kwargs = db.engine_kwargs("postgresql+psycopg://u:p@host:5432/x")
    timeout = kwargs["connect_args"]["connect_timeout"]
    assert 0 < timeout <= 30, f"connect_timeout {timeout} is unbounded or uselessly long"


def test_a_long_lived_pool_cannot_hold_a_silently_dead_socket():
    """The scheduler sleeps for hours between ticks. A dropped socket is never closed, only
    never answered again, and pool_pre_ping's SELECT 1 would hang on it exactly as the
    connect did. Keepalives turn a dead peer into an error; pool_recycle retires sockets old
    enough to have gone stale unnoticed."""
    kwargs = db.engine_kwargs("postgresql+psycopg://u:p@host:5432/x")
    assert kwargs["connect_args"]["keepalives"] == 1
    assert 0 < kwargs["connect_args"]["keepalives_idle"] <= 60
    assert 0 < kwargs["pool_recycle"] <= 3600


def test_sqlite_gets_none_of_it():
    """Tests and `--dry-run` run on sqlite, which rejects every one of these options. A fix
    that takes the scheduler off the hang and puts the test suite on a TypeError is not a
    fix."""
    kwargs = db.engine_kwargs("sqlite://")
    assert "connect_args" not in kwargs
    assert "pool_recycle" not in kwargs
    assert kwargs["pool_pre_ping"] is True


def test_connecting_to_a_blackhole_raises_instead_of_hanging(monkeypatch):
    """THE BEHAVIOUR, not the configuration.

    If connect_args never reached libpq this test would HANG rather than fail — which is
    precisely what makes it connected to the thing under test. A test that can only fail by
    timing out the suite is still a test; a green suite here means the connect came back.
    """
    monkeypatch.setattr(db, "DB_CONNECT_TIMEOUT_S", 2)
    engine = create_engine(BLACKHOLE, **db.engine_kwargs(BLACKHOLE))

    started = time.monotonic()
    with pytest.raises(SQLAlchemyError):
        with engine.connect():
            pass
    elapsed = time.monotonic() - started

    # 2s bound + generous slack for libpq retry and a loaded CI box. The assertion that
    # matters is "bounded at all" — before the fix this never returned.
    assert elapsed < 20, f"connect took {elapsed:.1f}s — the bound is not being applied"
