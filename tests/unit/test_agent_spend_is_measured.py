"""The agent spend meter read $0.00 for the life of the system, and hermes had been
measuring all along.

`week_to_date_spend_cents` sums `agent_runs.cost_cents`. Nothing in production ever wrote that
column: `start_run`/`finish_run` have no call site outside tests, because the three brain jobs
are native `hermes cron` entries whose whole payload is a prompt — there is no Python wrapper
around a cron firing, and the plugin's only hook is `pre_tool_call`. So every production row
was opened by `record_tool_call_for_session` as
`job='telegram-session' / trigger='manual' / status='running'`:

  * the digest printed "agent - week to date: $0.00 - weekly cap $15.00" every night, a
    measured-looking zero for a quantity nothing measured
  * the A6.2 weekly cap could never engage at any spend, because its meter was disconnected
  * migration 0009 added `trigger` so a cron firing could be told from a run-now, and the
    'cron' side of that comparison was structurally unsuppliable

THE MEASUREMENT ALREADY EXISTED. hermes-agent's `sessions` table carries `input_tokens`,
`output_tokens`, `estimated_cost_usd`, `actual_cost_usd`, `cost_status` and `api_call_count`
per session, and names a cron session `cron_<jobid>_<YYYYMMDD>_<HHMMSS>` where `<jobid>` is
the id `hermes cron list` prints beside the job's name. So the meter needed reading, not
inventing, and the join is direct rather than inferred.

STALE IS NOT ZERO, and the store says so itself: a real row carries
`estimated_cost_usd = 0.0` with `cost_status = 'unknown'`.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "hermes-brain"))

import report_agent_runs as rar  # noqa: E402

from plugins.artec.agent_runs import job_and_trigger_for, spend_posture  # noqa: E402

CRON_LIST = """
  22cf30408fee [active]
    Name:      learn-ideate
    Schedule:  0 7 * * 0
  dacb65fa6fae [active]
    Name:      nightly-digest
    Schedule:  0 21 * * 1-6
"""


# --- the label, readable from the session id with no I/O ---------------------------------

def test_a_cron_session_is_not_labelled_as_an_operator_chat():
    """THE DEFECT. Every row said `telegram-session` / `manual`, so Sunday's firing and a
    Tuesday chat were textually identical — and `trigger` exists precisely to tell them
    apart."""
    job, trigger = job_and_trigger_for("cron_22cf30408fee_20260809_070000")
    assert trigger == "cron"
    assert job == "cron:22cf30408fee"


def test_an_operator_session_still_reads_as_manual():
    assert job_and_trigger_for("telegram:12345") == ("telegram-session", "manual")
    assert job_and_trigger_for(None) == ("telegram-session", "manual")


def test_an_unresolvable_cron_id_is_carried_not_invented():
    """An id with no name attached is still a fact; a plausible name would be a fabrication."""
    job, trigger = job_and_trigger_for("cron_")
    assert trigger == "cron" and "unknown" in job


# --- the join: cron listing supplies the name, the store supplies the cost ----------------

def test_the_job_name_comes_from_the_cron_listing(monkeypatch):
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    assert rar.cron_job_names() == {"22cf30408fee": "learn-ideate",
                                    "dacb65fa6fae": "nightly-digest"}


def test_a_missing_hermes_binary_yields_no_names_rather_than_wrong_ones(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(rar.subprocess, "run", _boom)
    assert rar.cron_job_names() == {}


def _store(tmp_path: Path, rows: list[tuple]) -> Path:
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    d = tmp_path / "profiles" / "artec-brain"
    d.mkdir(parents=True)
    conn = sqlite3.connect(d / "state.db")
    conn.execute("""CREATE TABLE sessions (id TEXT, source TEXT, model TEXT,
        input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT,
        estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
        api_call_count INT, started_at REAL, ended_at REAL, end_reason TEXT)""")
    conn.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return tmp_path


def test_a_real_cost_is_read_and_converted_to_cents(tmp_path, monkeypatch):
    """$1.229... -> 123c. The number the digest should have been printing all along."""
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    home = _store(tmp_path, [
        ("cron_22cf30408fee_20260809_070000", "cron", "claude-opus-5", 41485, 11619,
         711168, 0, 1.229299, None, "estimated", 19, 1785514534.0, 1785516714.0,
         "cron_complete"),
    ])
    rows = rar.read_cron_sessions(home)
    assert len(rows) == 1
    row = rows[0]
    assert row["job"] == "learn-ideate" and row["trigger"] == "cron"
    assert row["status"] == "ok"
    assert row["cost_cents"] == 123
    assert row["tokens"] == 41485 + 11619
    assert row["started_at"] is not None and row["finished_at"] is not None


def test_an_unknown_cost_is_NULL_never_zero(tmp_path, monkeypatch):
    """THE INVARIANT. A real session in the wild carries estimated_cost_usd = 0.0 with
    cost_status = 'unknown'. Recording that as 0 makes an unmeasured week indistinguishable
    from a free one, which is the defect being fixed, rebuilt one layer down."""
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    home = _store(tmp_path, [
        ("cron_22cf30408fee_20260809_070000", "cron", "m", 31660, 14275, 0, 0,
         0.0, None, "unknown", 26, 1784859195.0, 1784859644.0, "cron_complete"),
    ])
    assert rar.read_cron_sessions(home)[0]["cost_cents"] is None


def test_an_actual_cost_wins_over_an_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    home = _store(tmp_path, [
        ("cron_22cf30408fee_20260809_070000", "cron", "m", 10, 10, 0, 0,
         9.99, 2.50, "estimated", 3, 1785883479.0, 1785883518.0, "cron_complete"),
    ])
    assert rar.read_cron_sessions(home)[0]["cost_cents"] == 250


def test_an_operator_chat_is_not_swept_up_as_a_cron_run(tmp_path, monkeypatch):
    """Only `source='cron'`. Charging the weekly JOB cap for the operator's own conversation
    would degrade scouting because somebody asked a question."""
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    home = _store(tmp_path, [
        ("telegram:900", "telegram", "m", 5, 5, 0, 0, 5.0, None, "estimated", 1,
         1785883479.0, 1785883518.0, None),
    ])
    assert rar.read_cron_sessions(home) == []


def test_a_session_for_a_deleted_cron_job_reports_its_id(tmp_path, monkeypatch):
    """A job removed after it ran still has sessions. Labelling those with a neighbouring
    name would be worse than saying which id it was."""
    monkeypatch.setattr(rar.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": CRON_LIST})())
    home = _store(tmp_path, [
        ("cron_ffffffffffff_20260809_070000", "cron", "m", 1, 1, 0, 0, 1.0, None,
         "estimated", 1, 1785883479.0, 1785883518.0, "cron_complete"),
    ])
    assert rar.read_cron_sessions(home)[0]["job"] == "cron:ffffffffffff"


# --- the posture must not read a disconnected meter as permission -------------------------

def test_a_blind_meter_does_not_license_full_spending():
    """0 of 1500c looks like a fresh week. If every run this week recorded NO cost, that 0 is
    unknown, not free — and a cap enforced against a disconnected meter is not a cap."""
    posture = spend_posture(0, 1500, blind=True, unmeasured_runs=4)
    assert posture["measured"] is False
    assert posture["unmeasured_runs"] == 4
    assert posture["degraded"] is True
    assert any("UNMEASURED" in a for a in posture["actions"])


def test_a_genuinely_idle_week_is_not_reported_as_blind():
    """The distinction has to cut both ways, or it is just a permanent warning."""
    posture = spend_posture(0, 1500)
    assert posture["measured"] is True and posture["degraded"] is False


def test_a_measured_week_still_degrades_at_the_thresholds():
    """The fix must not disturb A6.2 itself."""
    assert spend_posture(900, 1500)["scouting"] is False        # 0.60 drop-scouting
    assert spend_posture(1300, 1500)["gate_style"] == "short"   # 0.85 shorten-gate
    assert spend_posture(1500, 1500)["gate_runs"] is True       # the invariant


@pytest.mark.parametrize("spent,cap", [(0, 0), (10, 0)])
def test_a_zero_cap_never_divides_by_zero(spent, cap):
    assert spend_posture(spent, cap)["fraction"] == 0.0
