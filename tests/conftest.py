"""Shared fixtures. All §11 env vars get dummy values BEFORE app.settings is imported so
boot validation passes in tests; individual tests clear the cache to test failure modes.
Unit tests run on in-memory SQLite (DECISIONS.md #7)."""

from __future__ import annotations

import os

DUMMY_ENV = {
    "DATABASE_URL": "sqlite://",
    "HERMES_API_TOKEN": "test-token",
    "PUBLIC_BASE_URL": "",
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "INFO",
    "ANTHROPIC_API_KEY": "test-anthropic-key-fixture-000",
    "ANTHROPIC_MODEL": "claude-test",
    "FAL_KEY": "test-fal-key",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
    "GOOGLE_DRIVE_ROOT_FOLDER_ID": "root123",
    "GOOGLE_SHARED_DRIVE_ID": "drive123",
    "UPLOAD_POST_API_KEY": "test-upload-key",
    "UPLOAD_POST_USER": "ArtecMy",
    "BREVO_API_KEY": "test-brevo-key",
    "BREVO_LIST_ID": "3",
    "BREVO_SENDER_EMAIL": "hello@artec.my",
    "BREVO_TEMPLATE_ID": "3",
    "TELEGRAM_BOT_TOKEN": "1234:test-telegram-token",
    "TELEGRAM_CHAT_ID": "42",
    "STRIPE_SECRET_KEY": "test-stripe-secret-key-0000",
    "STRIPE_WEBHOOK_SECRET": "test-stripe-webhook-secret",
    "BILLPLZ_API_KEY": "test-billplz-key",
    "BILLPLZ_COLLECTION_ID": "coll1",
    "BILLPLZ_XSIGNATURE_KEY": "test-xsig-key",
}
os.environ.update(DUMMY_ENV)

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import seed_config  # noqa: E402
from app.models import V_BRIEF_SQL, Base  # noqa: E402


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text(V_BRIEF_SQL))
        conn.commit()
    return eng


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        seed_config(s)
        s.commit()
        yield s


@pytest.fixture
def repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------------------
# Real-Postgres substrate. Postgres-only semantics (advisory locks, sequences, jsonb) have
# NO SQLite equivalent — a SQLite test for them proves nothing, which is the exact
# tested-one-way/deployed-another class that has already put defects into production.
# CI sets TEST_DATABASE_URL to the throwaway Postgres it already stands up.
# ---------------------------------------------------------------------------------------
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def _normalize_pg(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


_DISPOSABLE_MARKERS = ("hermes_ci", "test", "scratch", "localhost", "127.0.0.1")


def _assert_disposable(url: str) -> None:
    """This fixture DROPS SCHEMA public CASCADE. Pointing TEST_DATABASE_URL at production
    would destroy it. Refuse anything that does not look unmistakably disposable — an
    irreversible action must not be one environment variable away."""
    lowered = url.lower()
    if not any(token in lowered for token in _DISPOSABLE_MARKERS):
        pytest.fail(
            "TEST_DATABASE_URL does not look like a disposable test database "
            f"(expected one of {_DISPOSABLE_MARKERS} in the URL). This fixture drops the "
            "public schema; refusing to run against a database that might be real."
        )
    for danger in ("railway.app", "rlwy.net", "amazonaws.com", "supabase"):
        if danger in lowered:
            pytest.fail(
                f"TEST_DATABASE_URL points at a hosted database ({danger}). This fixture "
                "drops the public schema — never run it against hosted infrastructure."
            )


@pytest.fixture
def pg_engine():
    """A real Postgres engine with the full schema. Skips when unavailable rather than
    silently falling back to SQLite — a skipped test is honest, a fallback is a lie."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set — Postgres-only test")
    _assert_disposable(TEST_DATABASE_URL)
    # NOT StaticPool. StaticPool hands every connect() the SAME underlying connection, and
    # Postgres advisory locks are session-scoped and re-entrant — so a "two replica" test
    # on a StaticPool engine is one session locking twice, which trivially succeeds and
    # proves nothing. CI caught this. A real pool gives genuinely distinct sessions.
    eng = create_engine(_normalize_pg(TEST_DATABASE_URL))
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(V_BRIEF_SQL))
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as s:
        seed_config(s)
        s.commit()
        yield s
