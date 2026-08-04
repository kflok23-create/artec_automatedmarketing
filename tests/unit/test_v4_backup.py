"""v4 2c-ii — the HTTPS mirrors, job 8 (`pg_dump`), and `restore-check`.

The claim under test throughout: **a backup you cannot prove is a backup you do not have.**
Every failure mode here is RED with a reason, and none of them degrades into a weaker check
that would report green.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.integrations.fakes import FakeDrive
from app.jobs import ARTEC, BRAIN, JOBS, NON_JOB_ROUTES, artec_jobs, mirror_paths
from app.stages import backup as backup_mod
from app.stages.backup import (
    VERIFIED_TABLES,
    BackupError,
    dump_filename,
    restore_check,
    rides_today,
    run_backup,
)

TOKEN = "test-token"


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


# ---- the mirrors: registry and routes must agree, in BOTH directions ---------------------

def test_every_artec_job_has_an_https_mirror():
    """A job body that only ever runs on a clock is a body nobody has exercised until the
    night it matters."""
    missing = [j.name for j in artec_jobs() if not j.mirror]
    assert not missing, f"artec-owned jobs with no HTTPS mirror: {missing}"


def test_every_declared_mirror_actually_exists_on_the_app():
    from app.main import app

    served = set(app.openapi()["paths"])
    missing = sorted(mirror_paths() - served)
    assert not missing, f"the registry claims mirrors the app does not serve: {missing}"


def test_no_commands_route_belongs_to_nobody():
    """The other direction. A `/commands` route with no job and no stated reason is a
    surface somebody exposed and nobody owns."""
    from app.main import app

    served = {p for p in app.openapi()["paths"] if p.startswith("/commands")}
    unaccounted = sorted(served - mirror_paths() - set(NON_JOB_ROUTES))
    assert not unaccounted, f"routes belonging to no job and no stated exception: {unaccounted}"


def test_there_are_twelve_jobs_and_ten_are_ours():
    assert len(JOBS) == 12
    # TEN artec-owned jobs, two brain cron. The tenth is the BESPOKE half of learn-ideate:
    # shadow mode runs both planners every Sunday, so that half fires on a clock too. A
    # first reconstruction had it brain-only and produced nine — the discrepancy was the
    # question worth asking, not a number to be adjusted until it matched.
    assert len(artec_jobs()) == 10, "ten HTTPS mirrors — the other two are brain cron"
    assert {j.owner for j in JOBS} == {ARTEC, BRAIN}
    assert len({j.number for j in JOBS}) == 12, "job numbers are unique"


def test_the_mirrors_are_all_behind_the_token():
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    for path in sorted(mirror_paths()):
        assert client.post(path).status_code in (401, 405), f"{path} is not behind the token"


# ---- job 8 · the dump --------------------------------------------------------------------

def test_a_missing_pg_dump_is_a_named_error_not_a_traceback(monkeypatch):
    """'postgres is reachable' is not evidence that pg_dump ships in this container — the
    ffprobe lesson, applied before it bites."""
    monkeypatch.setattr(backup_mod.shutil, "which", lambda _b: None)
    with pytest.raises(BackupError) as e:
        run_backup("postgresql://x/y", log=lambda *_: None)
    assert "pg_dump is not on PATH" in str(e.value)
    assert "postgresql-client" in str(e.value)


def test_the_dump_is_custom_format_because_that_is_what_restore_check_proves(
        monkeypatch, tmp_path):
    monkeypatch.setattr(backup_mod.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(backup_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    seen = {}

    def runner(cmd):
        seen["cmd"] = cmd
        open(cmd[cmd.index("--file") + 1], "wb").write(b"PGDMP fake dump bytes")
        return Completed(0)

    drive = FakeDrive()
    result = run_backup("postgresql://u:p@h/db", drive=drive, runner=runner,
                        log=lambda *_: None)
    assert "--format=custom" in seen["cmd"]
    assert result["bytes"] > 0 and result["uploaded"] is True
    assert drive.uploaded[0][1] == "_backups"
    assert result["filename"].startswith("artec-") and result["filename"].endswith(".dump")


def test_a_zero_byte_dump_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(backup_mod.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(backup_mod.tempfile, "gettempdir", lambda: str(tmp_path))

    def runner(cmd):
        open(cmd[cmd.index("--file") + 1], "wb").close()
        return Completed(0)

    with pytest.raises(BackupError, match="zero-byte"):
        run_backup("postgresql://x/y", runner=runner, log=lambda *_: None)


def test_a_failing_pg_dump_never_echoes_the_connection_string(monkeypatch, tmp_path):
    monkeypatch.setattr(backup_mod.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(backup_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    url = "postgresql://user:SUPERSECRET@host/db"

    def runner(cmd):
        return Completed(1, stderr=f"could not connect to {url}")

    with pytest.raises(BackupError) as e:
        run_backup(url, runner=runner, log=lambda *_: None)
    assert "SUPERSECRET" not in str(e.value) and "<redacted>" in str(e.value)


def test_restore_check_rides_job_8_on_the_first_only():
    assert rides_today(datetime(2026, 9, 1, tzinfo=UTC)) is True
    for day in (2, 15, 28, 30):
        assert rides_today(datetime(2026, 9, day, tzinfo=UTC)) is False


def test_the_filename_is_sortable_and_utc():
    name = dump_filename(datetime(2026, 8, 4, 3, 0, tzinfo=UTC))
    assert name == "artec-20260804T030000Z.dump"


# ---- restore-check · every refusal is RED with a reason ----------------------------------

def _dump(tmp_path, size=4096):
    path = tmp_path / "artec.dump"
    path.write_bytes(b"x" * size)
    return str(path)


def test_a_missing_dump_is_red(session, tmp_path):
    result = restore_check(session, "postgresql://x/y", str(tmp_path / "nope.dump"),
                           log=lambda *_: None)
    assert result.ok is False and "nothing to restore" in result.detail


def test_insufficient_disk_refuses_BEFORE_creating_anything(session, tmp_path):
    """Do not attempt and fail halfway — a half-written database is the next incident."""
    result = restore_check(session, "postgresql://x/y", _dump(tmp_path),
                           disk_free=lambda: 100, log=lambda *_: None)
    assert result.ok is False
    assert "insufficient free disk" in result.detail
    assert result.scratch_database == "", "nothing was created"


def test_create_database_denied_is_RED_and_never_degrades_to_a_schema_restore(
        session, tmp_path, monkeypatch):
    """A schema-scoped restore rewrites the dump's qualification, so it would verify a
    DIFFERENT artefact from the one an incident uses. That is a proof of the wrong thing,
    which is worse than no proof because it reports green."""
    def boom(*a, **k):
        raise RuntimeError("permission denied for database postgres")

    monkeypatch.setattr(backup_mod, "create_engine", boom)
    result = restore_check(session, "postgresql://x/y", _dump(tmp_path),
                           disk_free=lambda: 10**12, log=lambda *_: None)
    assert result.ok is False
    assert "CREATE DATABASE denied" in result.detail
    assert "DOES NOT FALL BACK TO A SCHEMA RESTORE" in result.remedy
    assert "CREATEDB" in result.remedy


def test_every_verified_table_is_named(session):
    """A dump that restores the posts but loses config or the price table is not a usable
    backup, so both are in the list."""
    assert "config" in VERIFIED_TABLES and "endpoint_prices" in VERIFIED_TABLES
    assert {"posts", "orders", "events", "metrics", "assets", "learnings"} <= set(VERIFIED_TABLES)


def test_live_counts_report_an_absent_table_as_none_never_zero(session):
    """stale ≠ zero, applied to the restore proof: a table that is not there is not a
    table with no rows."""
    from sqlalchemy import text

    counts = backup_mod.live_counts(session)
    assert counts["posts"] == 0
    session.execute(text("DROP TABLE metrics"))
    assert backup_mod.live_counts(session)["metrics"] is None


def test_the_scratch_database_name_is_unique_and_obviously_disposable():
    now = datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
    name = f"hermes_restore_check_{now.strftime('%Y%m%d%H%M%S')}"
    assert name == "hermes_restore_check_20260901030000"
    assert "restore_check" in name, "the name must say what it is to anyone who finds it"


def test_the_admin_url_targets_the_same_instance_and_a_different_database():
    url = backup_mod._admin_url("postgresql://u:p@host:5432/railway", "scratch_db")
    assert url == "postgresql://u:p@host:5432/scratch_db"
    assert "railway" not in url.rsplit("/", 1)[1], "the live database is never the target"


# ---- the CLI surface ---------------------------------------------------------------------

def test_the_jobs_command_lists_all_twelve():
    from typer.testing import CliRunner

    from app.cli import cli

    result = CliRunner().invoke(cli, ["jobs"])
    assert result.exit_code == 0
    for job in JOBS:
        assert job.name in result.stdout
    assert "brain — hermes cron" in result.stdout


def test_the_numbering_is_confirmed_and_the_caveat_has_been_retired():
    """The registry carried "RECONSTRUCTED, NOT RECOVERED" until the numbering was
    confirmed at 2c-iv. A caveat that outlives its reason becomes noise, and noise is what
    people learn to skip."""
    import app.jobs as jobs_mod

    assert "RECONSTRUCTED, NOT RECOVERED" not in (jobs_mod.__doc__ or "")
    assert "NUMBERING CONFIRMED" in (jobs_mod.__doc__ or "")
    assert os.path.exists("app/jobs.py")


def test_every_artec_job_has_a_firing_time_except_the_slot_driven_one():
    """Job 7 is slot-driven and has no fixed time by design. Every other artec job must
    have one, or it is a job that never fires."""
    timeless = [j.number for j in artec_jobs() if not j.at]
    assert timeless == [7], f"artec jobs with no firing time: {timeless}"


def test_the_scheduler_lists_its_own_next_runs_with_an_explicit_offset():
    """Registration is only ever proven by LISTING — `cron create` has been observed
    rejecting SUN while exiting 0. The artec side has no external registry, so it lists
    itself, and every timestamp carries +08:00 so a timezone regression is visible."""
    from app.scheduler import next_runs

    rows = next_runs()
    assert len(rows) == 9, "nine artec jobs have a fixed time; job 7 is slot-driven"
    assert all("+08:00" in r["next_run"] for r in rows)
    sunday = [r for r in rows if r["at"].startswith("0 ")]
    assert sunday, "the Sunday jobs must resolve to a Sunday"
    for row in sunday:
        from datetime import datetime

        assert datetime.fromisoformat(row["next_run"]).weekday() == 6
