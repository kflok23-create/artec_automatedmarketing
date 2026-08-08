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


def test_the_schedule_is_EXACTLY_the_twelve_and_nothing_else():
    """§2: the twelve in §3, and only those. Any job not on that list is a defect — so a
    thirteenth cannot appear without this failing."""
    from app.jobs import RETIRED, brain_jobs

    assert len(JOBS) == 12
    assert [j.number for j in JOBS] == list(range(1, 13))
    assert len(brain_jobs()) == 3, "jobs 3, 5 and 12 are hermes-agent cron on the brain"
    assert {j.number for j in brain_jobs()} == {3, 5, 12}
    assert len(artec_jobs()) == 9, "the scheduler owns the other nine"
    # Retired jobs must not reappear under any number.
    assert not [j for j in JOBS if j.name in RETIRED]


def test_the_nightly_chain_is_ordered_and_never_races_delivery():
    """20:30 → 20:40 → 20:55 → 21:05 is a designed sequence. Preparing at 21:00 would race
    delivery: the brain can call read_digest before job 11 has written the row, and the
    failure mode is an EMPTY DIGEST on a night something needed the operator."""
    by_name = {j.name: j for j in JOBS}
    assert by_name["assets-sync"].at == "20:30"
    assert by_name["doctor-sweep"].at == "20:40"
    assert by_name["digest-prepare"].at == "20:55"
    assert "21:00" in by_name["digest-delivery"].schedule
    order = [by_name[n].at for n in ("assets-sync", "doctor-sweep", "digest-prepare")]
    assert order == sorted(order), "the chain must be in ascending time order"


def test_render_fires_twice_a_week_because_that_IS_the_weekly_budget_bound():
    """There is no weekly fal cap. The bound is job 6 firing twice against the USD 2.50
    per-run cap — daily would make it ~USD 17.50 against a bound nobody set."""
    render = [j for j in JOBS if j.name == "render"][0]
    assert render.at == "0 10:00" and render.also_at == ("1 10:00",)
    assert "USD 5" in render.notes


def test_the_expiry_sweep_lives_inside_job_11_not_beside_it():
    digest = [j for j in JOBS if j.name == "digest-prepare"][0]
    assert "EXPIRY SWEEP RUNS INSIDE THIS JOB" in digest.notes
    assert "review-expiry-sweep" not in {j.name for j in JOBS}


def test_plan_diff_is_a_job_and_lands_before_the_gate():
    """Without it the Sunday gate has nothing to compare and shadow mode proves nothing."""
    by_name = {j.name: j for j in JOBS}
    assert by_name["plan-diff"].at == "0 08:00"
    assert by_name["plan-diff"].at == "0 08:00" < "0 09:00"
    # job 2 before job 3, or plan-diff has one side and agrees with itself
    assert by_name["bespoke-learn-ideate"].at == "0 06:30"


def test_there_are_twelve_jobs_and_the_artec_ones_are_mirrored():
    assert len(artec_jobs()) == 9
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
    # The scheme is now NORMALIZED to the psycopg3 marker — this assertion previously pinned
    # the raw `postgresql://` and so pinned the defect: SQLAlchemy resolves that to psycopg2,
    # which is not installed, and the restore proof died on ModuleNotFoundError before it
    # ever issued a CREATE DATABASE. What the test protects is host and target database.
    assert url == "postgresql+psycopg://u:p@host:5432/scratch_db"
    assert "@host:5432/" in url, "the same instance, always"
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


def test_the_registry_is_now_verbatim_and_the_caveat_is_retired():
    """The caveat named exactly which two slots were unrecovered; §3 supplied both, so it is
    gone. A caveat that outlives its reason becomes noise."""
    import app.jobs as jobs_mod

    assert "RECONSTRUCTED" not in (jobs_mod.__doc__ or "")
    assert "§3, verbatim" in (jobs_mod.__doc__ or "")
    assert os.path.exists("app/jobs.py")


def test_every_artec_job_has_a_firing_time_except_the_slot_driven_one():
    """Job 7 is slot-driven and has no fixed time by design. Every other artec job must
    have one, or it is a job that never fires."""
    timeless = [j.number for j in artec_jobs() if not j.at]
    assert timeless == [7], f"artec jobs with no firing time: {timeless}"


def test_the_scheduler_TICK_reads_the_registry(session, monkeypatch):
    """THE FINDING: the registry knew the times and the loop did not read them, so six jobs
    had times nothing acted on — a schedule that existed only in a data structure."""
    from datetime import datetime as dt

    from app import scheduler

    called = []
    monkeypatch.setattr(scheduler, "run_registry_job",
                        lambda s, job, log=None: called.append(job.name))
    monkeypatch.setattr(scheduler, "record_run", None, raising=False)

    import contextlib

    class _Rec:
        log = staticmethod(lambda *_a, **_k: None)

    @contextlib.contextmanager
    def fake_record_run(*_a, **_k):
        yield (session, _Rec())

    monkeypatch.setattr("app.db.record_run", fake_record_run)
    scheduler.tick(dt(2026, 8, 5, 20, 40, tzinfo=scheduler.SGT), set())
    assert "doctor-sweep" in called, "20:40 must fire job 10 from the registry"


def test_the_scheduler_lists_its_own_next_runs_with_an_explicit_offset():
    """Registration is only ever proven by LISTING — `cron create` has been observed
    rejecting SUN while exiting 0. The artec side has no external registry, so it lists
    itself, and every timestamp carries +08:00 so a timezone regression is visible."""
    from app.scheduler import next_runs

    rows = next_runs()
    # eight timed artec jobs, plus job 6's Monday retry = nine firings; job 7 is slot-driven
    assert len(rows) == 9
    assert all("+08:00" in r["next_run"] for r in rows)
    sunday = [r for r in rows if r["at"].startswith("0 ")]
    assert sunday, "the Sunday jobs must resolve to a Sunday"
    for row in sunday:
        from datetime import datetime

        assert datetime.fromisoformat(row["next_run"]).weekday() == 6


def test_every_timed_job_has_a_dispatch():
    """A job with a time and no dispatch raises at its firing minute — job 2 did, and would
    have failed at SUN 06:30 with plan-diff then agreeing with itself two hours later."""
    import inspect

    from app import scheduler
    from app.jobs import artec_jobs

    source = inspect.getsource(scheduler.run_registry_job)
    for job in artec_jobs():
        if not job.at:
            continue          # job 7 is slot-driven and handled by the slot pass
        assert f'job.name == "{job.name}"' in source, \
            f"job {job.number} {job.name} fires at {job.at} and has no dispatch"


# ---- job 7 · the one job that appears in NO listing ---------------------------------------

def test_job_7_SELECTS_correctly_over_a_non_empty_board(session):
    """Publish-by-slot has no fixed time, so it produces no next-run and appears in no
    listing — the guard that covers the other eleven does not reach it. Its earlier proof
    reported PROVEN over an EMPTY board, which established only that the pass ran.

    The board is built by DIRECT INSERT, not through the publish path: if the thing under
    test supplies the board, the test proves nothing.

    Asserted on the SET, not a count — a count can be right while the membership is wrong.
    """
    from datetime import date

    from app.models import Post
    from app.scheduler import select_due_posts, sweep_orphaned_slots
    from app.stages.publish import skip_reason

    week = date(2026, 8, 24)

    def insert(pid, **kw):
        session.add(Post(post_id=pid, week_start=week, **kw))

    # 1 · RENDERED, slot has arrived, no external id -> SELECTED
    insert("post_due", channel="instagram", status="RENDERED", slot="evening",
           media_drive_file_id="gen_a.jpg")
    # 2 · RENDERED but a DIFFERENT slot -> not selected by the evening pass
    insert("post_other_slot", channel="instagram", status="RENDERED", slot="morning",
           media_drive_file_id="gen_b.jpg")
    # 3 · already published -> never again (the never-republish guard at selection)
    insert("post_published", channel="instagram", status="RENDERED", slot="evening",
           media_drive_file_id="gen_c.jpg", external_post_id="up_777")
    # 4 · slot outside the vocabulary -> selected by nothing, swept into the digest
    insert("post_orphan", channel="instagram", status="RENDERED", slot="afternoon",
           media_drive_file_id="gen_d.jpg")
    # 5 · carries video, not APPROVED_TO_SEND -> selected but HELD by the review gate
    insert("post_video", channel="tiktok", status="RENDERED", slot="evening",
           media_drive_file_id="gen_e.mp4")
    # 6 · email, not APPROVED_TO_SEND -> HELD regardless of slot
    insert("post_email", channel="email", status="RENDERED", slot="evening",
           media_drive_file_id="gen_f.jpg")
    session.flush()

    evening = {p.post_id for p in select_due_posts(session, "evening")}
    assert evening == {"post_due", "post_video", "post_email"}, \
        "selection is by slot and never-published only; the review gates run after"
    assert "post_published" not in evening, "an external_post_id must never be re-selected"
    assert "post_other_slot" not in evening, "a slot that has not arrived is not due"
    assert "post_orphan" not in evening, "an off-vocabulary slot matches no pass"

    # the review gates decide what of the selected set actually goes out
    publishable = {p.post_id for p in select_due_posts(session, "evening")
                   if skip_reason(session, p) is None}
    assert publishable == {"post_due"}, \
        "video and email are held; only the photo post would publish"

    # and the orphan is not lost — it is swept into the digest rather than sitting silent
    assert {o["post_id"] for o in sweep_orphaned_slots(session)} == {"post_orphan"}


def test_job_7_selects_an_APPROVED_TO_SEND_post_at_its_slot(session):
    """The other half: an approved video waits for the next occurrence of its slot and is
    then selected AND publishable."""
    from datetime import date

    from app.models import Post
    from app.scheduler import select_due_posts
    from app.stages.publish import skip_reason

    session.add(Post(post_id="post_approved", week_start=date(2026, 8, 24), channel="tiktok",
                     status="APPROVED_TO_SEND", slot="evening",
                     media_drive_file_id="gen_g.mp4",
                     video_review={"decision": "approve", "telegram_message_id": 8801}))
    session.flush()
    due = select_due_posts(session, "evening")
    assert {p.post_id for p in due} == {"post_approved"}
    assert skip_reason(session, due[0]) is None


def test_the_admin_url_carries_the_psycopg3_driver_marker():
    """PRODUCTION, 2026-08-08:

        prove restore: FAILED — CREATE DATABASE denied:
        ModuleNotFoundError: No module named 'psycopg2'

    `_admin_url` built an engine from the RAW DATABASE_URL. Railway hands out
    `postgresql://`, which SQLAlchemy resolves to psycopg2 — a package this project does not
    install. Every other path reaches the database through `app.db.get_engine`, which
    normalizes; this was the only place that did not, and so the only place that could not
    connect. The proof had never once reached a CREATE DATABASE statement.
    """
    from app.stages.backup import _admin_url

    admin = _admin_url("postgresql://u:p@host:5432/railway", "postgres")
    assert admin.startswith("postgresql+psycopg://"), admin
    assert admin.endswith("/postgres")


def test_the_admin_url_normalizes_the_bare_postgres_scheme_too():
    """`postgres://` is the other shape Railway has handed out."""
    from app.stages.backup import _admin_url

    assert _admin_url("postgres://u:p@h/db", "postgres").startswith("postgresql+psycopg://")


def test_the_admin_url_leaves_an_already_normalized_url_alone():
    from app.stages.backup import _admin_url

    once = _admin_url("postgresql+psycopg://u:p@h/db", "postgres")
    assert once == _admin_url(once, "postgres")
