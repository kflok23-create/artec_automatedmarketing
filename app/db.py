"""Engine/session plumbing and the `runs` audit trail.

DATABASE_URL comes from Railway's reference variable (internal host). Never hardcoded.
Railway's filesystem is ephemeral — /tmp is scratch only; Postgres is the only store.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Run
from app.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def normalize_url(url: str) -> str:
    """Railway hands out postgresql:// — psycopg3 needs the +psycopg driver marker."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# A CONNECT WITH NO TIMEOUT IS NOT A CONNECT THAT FAILS — IT IS ONE THAT NEVER ANSWERS.
#
# 2026-08-08 09:05:14 UTC, artec-scheduler logged "Starting Container" and then nothing at
# all for nineteen minutes. It was not crashed: 66 MB resident, 0% CPU, ~0 bytes transmitted.
# It was blocked inside wait_for_schema() on the first connection, before the boot banner
# that every healthy deploy prints 0.3s after start. Nine jobs sat behind it.
#
# libpq's connect_timeout defaults to 0, which means WAIT FOREVER. So the retry loop in
# wait_for_schema — which catches Exception, announces the wait and polls — could never run,
# because the call it was written to retry never returned and never raised. That loop looked
# like a guard and was connected to nothing.
#
# It is the mistake already recorded in scheduler.py about the boot-time match probe:
# "`try/except Exception` does not catch a hang, and I reached for it as though it did." The
# same sentence applies to the function written to make boot safe. Bounding the connect is
# what turns the hang into an exception the existing loop can already handle.
#
# KEEPALIVES matter for THIS service specifically: the scheduler holds a pooled connection
# across hours of sleeping between ticks. A silently dropped socket is not closed, it is
# merely never answered again, and pool_pre_ping's SELECT 1 would then hang exactly as the
# connect did. keepalives turn a dead peer into an error; pool_recycle retires sockets old
# enough to have gone stale unnoticed.
DB_CONNECT_TIMEOUT_S = int(os.environ.get("DB_CONNECT_TIMEOUT_S", "10"))


def engine_kwargs(url: str) -> dict:
    """Engine options for a normalized URL. Postgres-only options are gated on the driver,
    because sqlite (tests, `--dry-run`) rejects every one of them."""
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["pool_recycle"] = 1800
        kwargs["connect_args"] = {
            "connect_timeout": DB_CONNECT_TIMEOUT_S,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        }
    return kwargs


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = normalize_url(get_settings().DATABASE_URL)
        _engine = create_engine(url, **engine_kwargs(url))
    return _engine


def set_engine(engine: Engine) -> None:
    """Test / dry-run hook: point the app at an in-memory engine."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def script_head_revision() -> str | None:
    """Head revision of the migration scripts, resolved from the INSTALLED package first.

    Migrations live inside the `app` package (app/migrations/) so the wheel ships them and
    this works regardless of CWD — reading alembic.ini from CWD only worked on Railway
    because the source tree happened to sit at /app. The alembic.ini fallback remains for
    odd invocation contexts.
    """
    from alembic.script import ScriptDirectory

    try:
        from pathlib import Path

        import app as _app

        migrations_dir = Path(_app.__file__).resolve().parent / "migrations"
        if migrations_dir.is_dir():
            return ScriptDirectory(str(migrations_dir)).get_current_head()
    except Exception:
        pass
    from alembic.config import Config as AlembicConfig

    return ScriptDirectory.from_config(AlembicConfig("alembic.ini")).get_current_head()


def migration_current() -> bool:
    """True when the DB's alembic_version matches the packaged head revision."""
    try:
        head = script_head_revision()
        with get_engine().connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return current is not None and current == head
    except Exception:
        return False


def db_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


class RunRecorder:
    """Collects human-readable progress lines and persists one `runs` row per command.
    Never stores env values — args are CLI arguments only, and the global redaction filter
    guards log lines besides."""

    def __init__(self, command: str, args: dict | None = None):
        self.command = command
        self.args = args or {}
        self.lines: list[str] = []
        self.started_at = datetime.now(UTC)
        # v4: fal spend in micro-dollars, set by the render stage. Queryable rather than
        # parsed back out of the log text.
        self.cost_micros: int | None = None

    def log(self, line: str) -> None:
        self.lines.append(line)
        print(line)

    def finish(self, session: Session, status: str) -> None:
        session.add(
            Run(
                command=self.command,
                args=self.args,
                started_at=self.started_at,
                finished_at=datetime.now(UTC),
                status=status,
                log=self.lines[-200:],
                cost_micros=self.cost_micros,
            )
        )
        session.flush()


@contextmanager
def record_run(command: str, args: dict | None = None) -> Iterator[tuple[Session, RunRecorder]]:
    """One transaction per command: work + its runs row commit (or roll back) together."""
    rec = RunRecorder(command, args)
    session = get_session_factory()()
    try:
        yield session, rec
        rec.finish(session, "ok")
        session.commit()
    except Exception as e:
        session.rollback()
        try:
            rec.lines.append(f"ERROR: {type(e).__name__}: {e}")
            rec.finish(session, "error")
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
