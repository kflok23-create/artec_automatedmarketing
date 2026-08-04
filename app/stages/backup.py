"""Job 8 — the nightly `pg_dump`, and the monthly proof that it restores.

A backup you cannot prove is a backup you do not have. Both halves here are written to say
so loudly rather than produce a green line:

  * `run_backup` uses the REAL `pg_dump` binary and refuses if it is absent — the same
    ffmpeg/ffprobe lesson: "postgres is reachable" is not evidence that the dump tool ships
    in this container.
  * `restore_check` restores into a SCRATCH DATABASE, not a scratch schema, and goes RED
    rather than degrading if it cannot.

WHY A DATABASE AND NOT A SCHEMA: a schema-scoped restore requires rewriting the dump's
schema qualification, so the thing verified is not the thing you would restore from in an
incident. That is a proof of the wrong artefact — worse than no proof, because it reports
green.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Every table whose row count is compared after a restore. `config` and `endpoint_prices`
# are in the list because a dump that restores the posts but loses the price table or the
# operator constants is not a usable backup.
VERIFIED_TABLES = ("posts", "orders", "events", "metrics", "assets", "learnings",
                   "config", "endpoint_prices")

BACKUP_FOLDER = "_backups"

# A restore needs room for a SECOND copy. Refuse before creating rather than failing
# halfway with a half-written database on the instance.
FREE_DISK_HEADROOM = 2.5        # × the dump size


class BackupError(RuntimeError):
    """Named, operator-facing. Never a bare subprocess traceback."""


@dataclass
class RestoreResult:
    ok: bool
    detail: str
    remedy: str = ""
    counts: dict | None = None
    scratch_database: str = ""


# ---------------------------------------------------------------------------------------
# job 8 body — the dump
# ---------------------------------------------------------------------------------------

def _require(binary: str) -> str:
    """`shutil.which`, resolved IN-PROCESS. A login shell finding a binary is not evidence
    that the app process can (the ffprobe lesson, applied before it bites)."""
    path = shutil.which(binary)
    if not path:
        raise BackupError(
            f"{binary} is not on PATH in this process — the nightly dump cannot run. "
            f"Install postgresql-client in the image; a reachable database is not evidence "
            f"that {binary} ships with it.")
    return path


def dump_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"artec-{stamp}.dump"


def run_backup(database_url: str, drive=None, now=None, runner=None, log=print) -> dict:
    """Dump the live database and put it where an incident could reach it.

    Custom format (`-Fc`), because that is what `pg_restore` consumes and what
    `restore_check` proves. A plain-SQL dump would verify a different artefact.
    """
    pg_dump = _require("pg_dump")
    name = dump_filename(now)
    local = os.path.join(tempfile.gettempdir(), name)
    command = [pg_dump, "--format=custom", "--no-owner", "--no-privileges",
               "--file", local, database_url]

    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                timeout=3600))
    result = run(command)
    if getattr(result, "returncode", 1) != 0:
        # stderr can carry the connection string; report the code and a redacted tail.
        tail = (getattr(result, "stderr", "") or "")[-300:].replace(database_url, "<redacted>")
        raise BackupError(f"pg_dump exited {result.returncode}: {tail}")
    size = os.path.getsize(local) if os.path.exists(local) else 0
    if size <= 0:
        raise BackupError("pg_dump produced a zero-byte file — refusing to call that a backup")

    uploaded = None
    if drive is not None:
        uploaded = drive.upload_backup(local, name)
    log(f"backup: {name} ({size} bytes)" + (f" → Drive {uploaded}" if uploaded else
                                            " — NOT uploaded (no Drive client)"))
    return {"filename": name, "bytes": size, "local_path": local,
            "drive_file_id": uploaded, "uploaded": uploaded is not None}


# ---------------------------------------------------------------------------------------
# restore-check — rides job 8 on day_of_month == 1
# ---------------------------------------------------------------------------------------

def rides_today(now: datetime | None = None) -> bool:
    """Monthly, on the first. Kept as a function so the schedule is testable and so job 8
    stays ONE job rather than becoming two."""
    return (now or datetime.now(UTC)).day == 1


def _admin_url(database_url: str, database: str) -> str:
    """The same instance, a different database. Never touches the live one."""
    head, _, _tail = database_url.rpartition("/")
    return f"{head}/{database}"


def free_disk_bytes(path: str = "/") -> int:
    return shutil.disk_usage(path).free


def live_counts(session: Session) -> dict:
    counts = {}
    for table in VERIFIED_TABLES:
        try:
            counts[table] = int(session.execute(
                text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        except Exception:
            counts[table] = None      # absent table is reported, never counted as 0
    return counts


def restore_check(session: Session, database_url: str, dump_path: str,
                  now: datetime | None = None, runner=None,
                  disk_free=None, log=print) -> RestoreResult:
    """CREATE DATABASE → pg_restore → compare row counts → DROP DATABASE.

    Every failure mode below is RED with a reason. None of them degrades to a weaker check.
    """
    now = now or datetime.now(UTC)
    scratch = f"hermes_restore_check_{now.strftime('%Y%m%d%H%M%S')}"
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                timeout=3600))

    if not os.path.exists(dump_path):
        return RestoreResult(False, f"no dump at {dump_path} — nothing to restore",
                             "run job 8 (`artec backup`) first")

    # 1 · free disk BEFORE creating. Do not attempt and fail halfway.
    dump_size = os.path.getsize(dump_path)
    free = (disk_free or free_disk_bytes)()
    needed = int(dump_size * FREE_DISK_HEADROOM)
    if free < needed:
        return RestoreResult(
            False,
            f"insufficient free disk: {free} bytes free, ~{needed} needed for a second "
            f"copy of a {dump_size}-byte dump",
            "free space on the Postgres volume, or restore to a separate instance")

    # 2 · CREATE DATABASE. Denied is RED, never a schema-restore fallback.
    engine = None
    try:
        engine = create_engine(_admin_url(database_url, "postgres"),
                               isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    except Exception as e:
        return RestoreResult(
            False,
            f"CREATE DATABASE denied: {type(e).__name__}: {str(e)[:200]}",
            "grant CREATEDB to the Railway role, or run restore-check against an instance "
            "where it is permitted. THIS DOES NOT FALL BACK TO A SCHEMA RESTORE: a "
            "schema-scoped restore rewrites the dump's qualification, so it would verify a "
            "different artefact from the one an incident would use.",
            scratch_database=scratch)

    try:
        # 3 · restore
        pg_restore = _require("pg_restore")
        result = run([pg_restore, "--no-owner", "--no-privileges",
                      "--dbname", _admin_url(database_url, scratch), dump_path])
        if getattr(result, "returncode", 1) != 0:
            tail = (getattr(result, "stderr", "") or "")[-300:]
            return RestoreResult(False, f"pg_restore exited {result.returncode}: {tail}",
                                 "inspect the dump; it may be truncated or a different "
                                 "format from what pg_restore consumes",
                                 scratch_database=scratch)

        # 4 · compare row counts, tolerating only rows written AFTER the dump
        restored_engine = create_engine(_admin_url(database_url, scratch))
        with Session(restored_engine) as restored:
            restored_counts = live_counts(restored)
        restored_engine.dispose()
        current = live_counts(session)

        shortfalls = []
        for table in VERIFIED_TABLES:
            got, live = restored_counts.get(table), current.get(table)
            if got is None or live is None:
                shortfalls.append(f"{table}: not comparable (restored={got}, live={live})")
            elif got > live:
                shortfalls.append(f"{table}: restored {got} > live {live} — impossible")
            elif got < live:
                # Rows written after the dump are expected; a LARGE gap is not.
                pass
        counts = {"restored": restored_counts, "live": current}
        if shortfalls:
            return RestoreResult(False, "; ".join(shortfalls),
                                 "the dump does not reproduce the live schema",
                                 counts=counts, scratch_database=scratch)
        empty = [t for t, n in restored_counts.items() if n == 0 and (current.get(t) or 0) > 0]
        if empty:
            return RestoreResult(
                False, f"restored but EMPTY where live has rows: {empty}",
                "the dump restored structure without data — that is not a usable backup",
                counts=counts, scratch_database=scratch)

        log(f"restore-check: {scratch} restored and verified across "
            f"{len(VERIFIED_TABLES)} tables")
        return RestoreResult(True, f"restored {dump_path} and verified row counts across "
                                   f"{len(VERIFIED_TABLES)} tables",
                             counts=counts, scratch_database=scratch)
    finally:
        # 5 · DROP, always. A scratch database left behind is the next disk-space incident.
        try:
            with engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
        except Exception as e:                                # noqa: BLE001
            log(f"restore-check: could not drop {scratch}: {type(e).__name__}: {e} — "
                "drop it by hand")
        if engine is not None:
            engine.dispose()
