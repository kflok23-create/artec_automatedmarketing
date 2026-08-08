"""A proof that only happens when a human remembers reports the state of somebody's
attention, not the state of the system.

`POST /commands/prove-all` existed and worked for days while the matrix stayed stale,
because running it needed an authenticated call. So the sweep now runs itself on the api at
boot — deliberately NOT on the scheduler, whose boot path gates nine jobs, and where I
removed exactly this for exactly that reason.

Everything below tests the gates that make an unattended sweep safe:

  * staleness, with BOTH SIDES NAMED — `now` is a parameter, never read inside
    (DECISIONS 112: a hidden clock is an unnamed side of a comparison)
  * `restore` excluded unless genuinely due, because it is the one proof that MUTATES THE
    SERVER (CREATE DATABASE / pg_restore / DROP DATABASE)
  * a skip must NEVER overwrite an earlier verdict
  * the thread must never be able to take the service down
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.stages.prove_sweep import (
    RESTORE_STALE_AFTER,
    SWEEP_STALE_AFTER,
    restore_is_due,
    sweep_is_due,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _at(delta: timedelta) -> dict:
    return {"video-pipeline": {"ok": True, "at": (NOW - delta).isoformat()}}


# --- staleness: both sides named --------------------------------------------------------

def test_never_run_is_due_not_fresh():
    """AN ABSENT STAMP MEANS NEVER RUN. Reading absence as fresh is how a sweep stops
    running and nothing says so — the same shape as the memory audit that scanned no files
    and called it clean."""
    due, why = sweep_is_due({}, NOW)
    assert due
    assert "ever been recorded" in why


def test_a_fresh_matrix_is_not_re_swept():
    """A burst of redeploys must sweep once, not once per deploy."""
    due, _ = sweep_is_due(_at(timedelta(hours=1)), NOW)
    assert not due


def test_a_stale_matrix_is_swept():
    due, _ = sweep_is_due(_at(SWEEP_STALE_AFTER + timedelta(minutes=1)), NOW)
    assert due


def test_the_clock_is_a_parameter_not_a_hidden_side():
    """DECISIONS 112. The same stored proofs must give opposite answers at two instants —
    which is only possible if `now` genuinely supplies one side of the comparison."""
    proofs = _at(timedelta(hours=1))
    assert not sweep_is_due(proofs, NOW)[0]
    assert sweep_is_due(proofs, NOW + SWEEP_STALE_AFTER)[0]


def test_an_unparseable_stamp_is_treated_as_absent_not_as_now():
    """A corrupt value read as 'now' would make the sweep permanently fresh and silently
    stop. Absent is the safe reading, because absent means DUE."""
    assert sweep_is_due({"video-pipeline": {"at": "not-a-date"}}, NOW)[0]
    assert sweep_is_due({"video-pipeline": {"at": None}}, NOW)[0]
    assert sweep_is_due({"video-pipeline": "not-even-a-dict"}, NOW)[0]


def test_a_naive_stamp_does_not_explode_the_comparison():
    """config.proofs has carried stamps from two writers (run_all and the brain's
    prove_brain.py). A naive datetime raises TypeError against an aware one, inside a boot
    thread, where the failure would be a log line nobody reads."""
    naive = {"video-pipeline": {"at": (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()}}
    assert not sweep_is_due(naive, NOW)[0]


def test_the_newest_stamp_wins_across_capabilities():
    """Nine capabilities write nine stamps. The sweep is as fresh as its most recent one,
    not its oldest — otherwise one never-proven capability pins it permanently due."""
    proofs = {"video-pipeline": {"at": (NOW - timedelta(days=40)).isoformat()},
              "brevo-send": {"at": (NOW - timedelta(minutes=5)).isoformat()}}
    assert not sweep_is_due(proofs, NOW)[0]


# --- restore rides a much slower clock ---------------------------------------------------

def test_restore_is_due_when_never_proven():
    assert restore_is_due({}, NOW)[0]


def test_restore_is_not_re_run_on_every_deploy():
    """THE POINT OF THE WHOLE GATE. restore does CREATE DATABASE / pg_restore / DROP
    DATABASE against the live instance. A sweep on every redeploy would turn a monthly
    operation into a per-deploy one, and that is the sort of thing discovered mid-incident."""
    proofs = {"restore": {"ok": True, "at": (NOW - timedelta(days=2)).isoformat()}}
    assert not restore_is_due(proofs, NOW)[0]


def test_restore_becomes_due_after_the_monthly_window():
    proofs = {"restore": {"ok": True, "at": (NOW - RESTORE_STALE_AFTER
                                             - timedelta(days=1)).isoformat()}}
    assert restore_is_due(proofs, NOW)[0]


def test_restore_freshness_ignores_other_capabilities():
    """A proven video-pipeline five minutes ago says nothing about restore. Reading the
    whole-matrix stamp here would keep restore permanently 'fresh' and it would never run
    again — a guard whose subject is absent while the check still passes."""
    proofs = {"video-pipeline": {"at": NOW.isoformat()}}
    assert restore_is_due(proofs, NOW)[0]


# --- the skip must not overwrite a verdict ----------------------------------------------

def test_skipping_restore_reports_not_provable_and_records_nothing(session, monkeypatch):
    """A SKIP IS NOT A VERDICT. If `include_restore=False` wrote anything to config.proofs,
    an unattended sweep would erase the last real restore proof twice a day — turning the
    monthly evidence into a permanent 'never'."""
    from app.config import get_config, set_config
    from app.stages import prove as prove_mod

    earlier = {"restore": {"ok": True, "detail": "round-tripped 1234 rows",
                           "at": "2026-08-01T03:00:00+00:00"}}
    set_config(session, "proofs", earlier, set_by="test")

    # Only the restore branch is under test; keep the other eight from doing real work.
    monkeypatch.setattr(prove_mod, "CAPABILITIES", ("restore",))
    result = prove_mod.run_all(session, settings=None, log=lambda *_: None,
                               include_restore=False)

    assert result["results"]["restore"]["state"] == "not_provable"
    assert "30-day cadence" in result["results"]["restore"]["detail"]
    # THE ASSERTION THAT MATTERS: the stored verdict is untouched.
    assert get_config(session, "proofs")["restore"]["detail"] == "round-tripped 1234 rows"


def test_skipping_restore_never_takes_a_dump(session, monkeypatch):
    """The dump is the expensive half. `include_restore=False` must not call run_backup at
    all — otherwise the gate saves the CREATE DATABASE and still pg_dumps every deploy."""
    from app.stages import prove as prove_mod

    called = []
    import app.stages.backup as backup_mod
    monkeypatch.setattr(backup_mod, "run_backup",
                        lambda *a, **k: called.append(1) or {"local_path": "/x", "bytes": 1,
                                                             "filename": "x"})
    monkeypatch.setattr(prove_mod, "CAPABILITIES", ("restore",))

    class _S:
        DATABASE_URL = "postgresql://u:p@h/db"

    prove_mod.run_all(session, settings=_S(), log=lambda *_: None, include_restore=False)
    assert not called, "run_backup was called despite include_restore=False"


# --- the thread must never take the service down -----------------------------------------

def test_a_failing_sweep_does_not_raise_into_the_service(monkeypatch):
    """Four outages on this project came from boot-path additions. The sweep's whole licence
    to exist at boot is that it cannot propagate — so this asserts the thread swallows a
    hard failure and the caller returns normally."""
    import app.stages.prove_sweep as sweep_mod

    def _boom(**_):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(sweep_mod, "run_sweep", _boom)
    thread = sweep_mod.start_boot_sweep()
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_the_boot_thread_is_a_daemon_and_is_never_joined(monkeypatch):
    """A non-daemon thread keeps the process alive on shutdown; a joined one blocks
    startup. Either would make the sweep able to hold the service, which is the one thing
    it must not do."""
    import app.stages.prove_sweep as sweep_mod

    monkeypatch.setattr(sweep_mod, "run_sweep", lambda **_: {"ran": False})
    thread = sweep_mod.start_boot_sweep()
    assert thread.daemon is True
    thread.join(timeout=10)


def test_main_starts_the_sweep_without_awaiting_it():
    """The wiring, asserted from outside both files. A sweep module nothing calls is the
    disconnected guard again — and this project has shipped that twice (the branch ruleset
    that applied to no branches, the validator only tests referenced)."""
    from pathlib import Path

    import app.main as main_mod

    source = Path(main_mod.__file__).read_text(encoding="utf-8")
    assert "start_boot_sweep()" in source, (
        "app/main.py does not start the boot sweep — prove_sweep.py would be correct logic "
        "wired to nothing, and the matrix would go stale exactly as before")
    assert "await start_boot_sweep" not in source and "thread.join" not in source


@pytest.mark.parametrize("bad", [{}, {"restore": {}}, None])
def test_the_gates_never_raise_on_a_malformed_proofs_row(bad):
    """config.proofs is written by two services. A shape either does not expect must degrade
    to 'due', never to an exception inside a boot thread."""
    assert sweep_is_due(bad, NOW)[0] is True
    assert restore_is_due(bad, NOW)[0] is True
