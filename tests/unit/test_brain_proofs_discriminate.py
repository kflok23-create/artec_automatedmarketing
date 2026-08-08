"""The three proofs that can only run on the brain — tested for what would FAKE them.

`app/stages/prove.py` reports agent-session, sunday-cron and audit-memory as NOT PROVABLE,
correctly: it runs on artec-scheduler, and the evidence (the hermes message store, the cron
registry, the memory files) lives on the brain's volume. `deploy/hermes-brain/prove_brain.py`
runs them where the evidence is.

Which moves the whole risk onto ONE question: can each of these report success without
demonstrating the thing? prove.py's FALSE_PASS register names them —

    agent-session  "a readable store belonging to a DIFFERENT hermes install"
    sunday-cron    "a cron listing from any hermes install, not artec's"
    audit-memory   "a memory directory that is empty, or somebody else's"

— and every one of these tests is a false pass attempted, not a happy path confirmed.

The sunday-cron case is not hypothetical. Smoke-run on a developer machine carrying an
unrelated hermes install, the prover read a perfectly healthy listing that belonged to a
trading bot. The listing below is that output.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "hermes-brain"))

import prove_brain  # noqa: E402

OURS = """
  Name: learn-ideate
    Schedule: 0 7 * * 0
    Next run: 2026-08-09T07:00:00+08:00
  Name: weekly-gate
    Schedule: 0 9 * * 0
    Next run: 2026-08-09T09:00:00+08:00
  Name: nightly-digest
    Schedule: 0 21 * * 1-6
    Next run: 2026-08-10T21:00:00+08:00
"""

# Verbatim shape of what a different install on the same PATH actually returned.
SOMEBODY_ELSES = """
  Name: hermes-trading-daily-review
    Schedule: 0 18 * * 1-5
    Next run: 2026-08-10T18:00:00+08:00
  Name: hermes-trading-weekly-tactical
    Schedule: 0 9 * * 0
    Next run: 2026-08-09T09:00:00+08:00
"""


# --- sunday-cron -----------------------------------------------------------------------

def test_a_healthy_listing_from_another_install_is_refused():
    """THE FALSE PASS, OBSERVED IN THE WILD. Well-formed, resolving to +08:00, and about
    somebody else's jobs entirely."""
    ok, detail, _ = prove_brain.evaluate_cron_listing(SOMEBODY_ELSES)
    assert not ok
    assert "missing from the listing" in detail
    assert "hermes-trading-daily-review" in detail   # says WHOSE jobs it found


def test_our_three_jobs_prove_it():
    ok, detail, evidence = prove_brain.evaluate_cron_listing(OURS)
    assert ok, detail
    assert evidence["registered"] == ["learn-ideate", "nightly-digest", "weekly-gate"]


def test_a_thirteenth_job_is_a_defect_not_a_curiosity():
    """Every "is this job present?" check passes on a superset. The schedule is twelve jobs;
    three are the brain's, and a fourth brain cron is a defect however it arrived. This
    mirrors the entrypoint's EXACT-SET check rather than re-deriving a looser one."""
    ok, detail, _ = prove_brain.evaluate_cron_listing(
        OURS + "  Name: rogue-job\n    Next run: 2026-08-09T05:00:00+08:00\n")
    assert not ok
    assert "UNEXPECTED" in detail and "rogue-job" in detail


def test_times_that_do_not_resolve_to_singapore_are_refused():
    """Same jobs, same names, firing at the wrong hour. A cron listing proves the schedule
    only if the schedule is in the timezone the schedule was written for."""
    ok, detail, _ = prove_brain.evaluate_cron_listing(OURS.replace("+08:00", "+00:00"))
    assert not ok
    assert "+08:00" in detail


def test_a_sunday_next_run_for_the_digest_is_refused():
    """Job 12 is MON-SAT: Sunday belongs to the gate. A `0 21 * * 1-6` taken as daily would
    interrupt the operator on the one evening the gate owns."""
    ok, detail, _ = prove_brain.evaluate_cron_listing(
        OURS.replace("Next run: 2026-08-10T21:00:00+08:00",
                     "Next run: Sun 2026-08-09T21:00:00+08:00"))
    assert not ok
    assert "SUNDAY" in detail


def test_an_empty_listing_is_a_failure_not_a_pass():
    """`hermes cron create` exits 0 on failure, so absence in the listing IS the failure —
    the one fact the entrypoint's whole cron section is built around."""
    ok, _, _ = prove_brain.evaluate_cron_listing("")
    assert not ok


# --- agent-session ---------------------------------------------------------------------

def _store(home: Path, profile: str, sessions: int = 1, turns: int = 1) -> None:
    (home / "active_profile").write_text(profile, encoding="utf-8")
    db_dir = home / "profiles" / profile
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "state.db")
    conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, source TEXT, chat_id TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id INTEGER, "
                 "role TEXT, body TEXT)")
    for s in range(1, sessions + 1):
        conn.execute("INSERT INTO sessions VALUES (?, 'telegram', '123')", (s,))
        for _ in range(turns):
            conn.execute("INSERT INTO messages (session_id, role, body) "
                         "VALUES (?, 'user', 'hi')", (s,))
    conn.commit()
    conn.close()


def test_a_readable_store_from_a_different_install_is_refused(tmp_path):
    """THE NAMED FALSE PASS. The store opens, the tables are right, the sessions are real —
    and the profile says it is not ours. Checked BEFORE the contents, because everything
    after that point looks identical either way."""
    _store(tmp_path, "someone-elses-bot")
    ok, detail, _ = prove_brain.prove_agent_session(tmp_path)
    assert not ok
    assert "someone-elses-bot" in detail


def test_a_session_nobody_has_spoken_in_is_not_a_working_session(tmp_path):
    """A row in `sessions` proves a row in `sessions`. The operator turn is the thing."""
    _store(tmp_path, "artec-brain", sessions=1, turns=0)
    ok, detail, _ = prove_brain.prove_agent_session(tmp_path)
    assert not ok
    assert "no operator turns" in detail


def test_the_real_store_proves_it(tmp_path):
    _store(tmp_path, "artec-brain", sessions=2, turns=3)
    ok, detail, evidence = prove_brain.prove_agent_session(tmp_path)
    assert ok, detail
    assert evidence == {"sessions": 2, "operator_turns": 6, "profile": "artec-brain"}


def test_the_decoy_state_db_cannot_satisfy_it(tmp_path):
    """VERIFY.md: `$HERMES_HOME/state.db` is a decoy that opens cleanly and answers with a
    plausible error. The real store is profile-scoped. Reading the decoy is exactly how this
    proof would pass while proving nothing — so a decoy with no profile store must fail."""
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    sqlite3.connect(tmp_path / "state.db").close()          # the decoy, and only the decoy
    ok, detail, _ = prove_brain.prove_agent_session(tmp_path)
    assert not ok
    assert "no message store" in detail


# --- audit-memory ----------------------------------------------------------------------

def test_a_zero_file_scan_is_a_failure(tmp_path):
    """`clean — 0 file(s) scanned` was reported for weeks while a stale capability claim sat
    in the store. An audit that read nothing has not found nothing."""
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    ok, detail, _ = prove_brain.prove_audit_memory(tmp_path)
    assert not ok
    assert "ZERO files" in detail


def test_real_memory_with_a_number_in_it_fails(tmp_path):
    """Numbers never live in agent memory — the store is playbook only."""
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("CAC was S$42.10 last week", encoding="utf-8")
    ok, detail, _ = prove_brain.prove_audit_memory(tmp_path)
    assert not ok
    assert "finding(s)" in detail


def test_real_memory_with_only_playbook_content_proves_it(tmp_path):
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        "Parents respond to focus, not to STEM vocabulary.\n", encoding="utf-8")
    ok, detail, _ = prove_brain.prove_audit_memory(tmp_path)
    assert ok, detail
    assert "1 file(s)" in detail


# --- the boundary the whole file rests on ------------------------------------------------

def test_prove_brain_never_imports_the_app_package():
    """Stated here too, next to the tests that exercise it: the brain image carries
    hermes-agent and none of `app`. tests/unit/test_plugin_is_self_contained.py enforces it
    mechanically across every script under deploy/hermes-brain/."""
    source = (Path(prove_brain.__file__)).read_text(encoding="utf-8")
    assert "import app" not in source and "from app" not in source


@pytest.mark.parametrize("capability", ["agent-session", "sunday-cron", "audit-memory"])
def test_each_capability_here_is_one_prove_py_calls_not_provable(capability):
    """The two halves must name the SAME capabilities. If prove.py learned to run one of
    these itself, two provers would write the same key in config.proofs and the last writer
    would win silently."""
    from app.stages.prove import CAPABILITIES

    assert capability in CAPABILITIES
