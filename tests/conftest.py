"""Shared fixtures. All §11 env vars get dummy values BEFORE app.settings is imported so
boot validation passes in tests; individual tests clear the cache to test failure modes.
Unit tests run on in-memory SQLite (DECISIONS.md #7)."""

from __future__ import annotations

import os
import pathlib

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
def engine(request):
    """SQLite by default; REAL POSTGRES when ARTEC_TEST_SUBSTRATE=postgres.

    Merge-gate condition 2 reads "every pg test executed and green". Eleven tests carried
    the `pg` marker because they exercise Postgres-ONLY semantics — advisory locks,
    sequences, jsonb. The other 459 had simply never run against Postgres at all, which is
    a coverage decision nobody made on purpose. This switch makes the whole suite runnable
    on the real substrate so the decision becomes deliberate and the evidence is CI output
    rather than a claim.
    """
    if os.environ.get("ARTEC_TEST_SUBSTRATE") == "postgres":
        url = os.environ.get("TEST_DATABASE_URL", "")
        if not url:
            pytest.fail("ARTEC_TEST_SUBSTRATE=postgres but TEST_DATABASE_URL is unset — "
                        "refusing to silently fall back to SQLite, which is the substrate "
                        "this run exists to avoid")
        _assert_disposable(url)
        eng = create_engine(_normalize_pg(url))
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        # MIGRATIONS, not create_all. THE FIRST FULL POSTGRES RUN FOUND THIS: six tests
        # failed with `relation "post_id_seq" does not exist`, because `create_all` builds
        # TABLES from model metadata and the post-id allocator is a SEQUENCE created by
        # migration 0004. Production's schema comes from `alembic upgrade head`; a fixture
        # that builds it another way is testing a schema that never ships.
        #
        # It also exposed a real asymmetry, recorded rather than papered over:
        # `next_post_number()` calls `ensure_allocator()` first on the SQLite branch and
        # goes straight to `nextval` on the Postgres branch. `ensure_allocator` itself is
        # dialect-aware and would create the sequence — the Postgres CALLER just never
        # invokes it. Production is protected only because pre-deploy runs the migrations.
        #
        # THE URL IS PASSED BY ENVIRONMENT, NOT BY CONFIG. First attempt set
        # cfg.set_main_option("sqlalchemy.url", ...) and the upgrade silently migrated
        # SQLite instead: `app/migrations/env.py` resolves the URL from settings and
        # IGNORES an explicitly-passed one. 6 failures became 210 errors
        # (`relation "posts" does not exist`). env.py is NOT changed here — production's
        # migration path is the one P-class-proven thing in this system and it does not get
        # touched in the same pass as a merge. Logged as gap S3 with a named follow-up.
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig

        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(
            pathlib.Path(__file__).resolve().parent.parent / "app" / "migrations"))
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = _normalize_pg(url)
        try:
            alembic_command.upgrade(cfg, "head")
        finally:
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior
        with eng.begin() as conn:
            conn.execute(text(V_BRIEF_SQL))
        yield eng
        eng.dispose()
        return
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text(V_BRIEF_SQL))
        conn.commit()
    yield eng


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


@pytest.fixture
def operator_session(tmp_path, monkeypatch):
    """A hermes-agent message store the agent did not author. The transcription guard reads
    THIS, not the `operator_message` argument the agent passes in — a check the agent
    supplies both sides of proves nothing.

    The schema mirrors the REAL store, probed against a live install (see VERIFY.md):
    $HERMES_HOME/state.db with sessions(id) and messages(session_id, role, content), where
    role is 'user' | 'assistant' | 'tool' and a tool result is NEVER role='user'.
    """
    import sqlite3

    # The PRODUCTION layout, not a convenient one: the store is profile-scoped, and
    # $HERMES_HOME/state.db is a decoy that opens cleanly and answers with a plausible
    # error. Both are built here so the tests run against the shape that actually ships.
    monkeypatch.delenv("ARTEC_TRANSCRIPT_DB", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    profile_dir = tmp_path / "profiles" / "artec-brain"
    profile_dir.mkdir(parents=True, exist_ok=True)
    decoy = sqlite3.connect(tmp_path / "state.db")     # the obvious path, wrong file
    decoy.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT)")
    decoy.commit()
    decoy.close()
    db = profile_dir / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT, "
        "session_key TEXT, chat_id TEXT, ended_at TEXT);"
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT, role TEXT, content TEXT, tool_name TEXT);")
    conn.commit()

    def _write(task_id: str, *operator_turns: str, assistant: tuple = (),
               tool: tuple = ()) -> str:
        # ended_at stays NULL: a Telegram session is long-lived and the digest
        # conversation runs inside an open one.
        conn.execute("INSERT OR IGNORE INTO sessions (id, source, session_key, chat_id) "
                     "VALUES (?, 'telegram', ?, '2111270140')",
                     (task_id, f"agent:main:telegram:dm:{task_id}"))
        for turn in operator_turns:
            conn.execute("INSERT INTO messages (session_id, role, content) "
                         "VALUES (?, 'user', ?)", (task_id, turn))
        for turn in assistant:
            conn.execute("INSERT INTO messages (session_id, role, content) "
                         "VALUES (?, 'assistant', ?)", (task_id, turn))
        for turn in tool:
            conn.execute("INSERT INTO messages (session_id, role, content, tool_name) "
                         "VALUES (?, 'tool', ?, 'read_digest')", (task_id, turn))
        conn.commit()
        return task_id

    yield _write
    conn.close()


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
