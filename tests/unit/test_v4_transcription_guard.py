"""v4 Amendment 1 — the agent is a transcriber, never an author.

THE BREACH THIS FILE CLOSES: the guard used to compare the agent's `figures` against the
agent's own `operator_message` argument. The agent supplied both sides, so the check was
comparing something against itself and reporting green — the same shape as the StaticPool
advisory-lock test that passed for the wrong reason.

THE RULE NOW, stated explicitly because it is no longer "the immediately preceding
message": every digit submitted must appear in SOME message the OPERATOR sent in this
session, and the operator_message audit string must itself be one of those messages. The
window widened because the confirm flow makes the last operator turn "yes" — and it
widened ONLY across the operator's own turns. Assistant turns are never a source.
"""

import importlib.util

import pytest

TASK = "task_7f31"
DATE = "2026-08-27"


@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_guard_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hook(tools, task_id, **args):
    args.setdefault("post_id", "post_1497")
    args.setdefault("channel", "facebook")
    args.setdefault("metric_date", DATE)
    return tools.pre_tool_call("record_metrics", args=args, task_id=task_id)


# ---- the required test: a message the operator never sent ---------------------------------

def test_a_fabricated_operator_message_is_blocked(tools, operator_session):
    """The agent invents both the figures AND the utterance they must appear in. Under the
    old guard this passed: the digits were 'present in the operator's message' because the
    agent wrote that message. It must be refused."""
    task = operator_session(TASK, "evening", "yes")          # the operator typed no figures
    verdict = hook(tools, task,
                   figures={"impressions": 9900, "clicks": 240},
                   operator_message="post_1497 did 9900 impressions and 240 clicks")
    assert verdict and verdict["action"] == "block"
    assert "not a message the operator sent" in verdict["message"]


def test_a_real_message_with_an_invented_figure_is_blocked(tools, operator_session):
    task = operator_session(TASK, "4200 impressions", "yes")
    verdict = hook(tools, task,
                   figures={"impressions": 4200, "clicks": 118},   # 118 never typed
                   operator_message="4200 impressions")
    assert verdict and verdict["action"] == "block"
    assert "clicks=118" in verdict["message"]
    assert "never sent in this session" in verdict["message"]


def test_an_assistant_turn_is_not_a_source(tools, operator_session):
    """The agent echoing a figure back does not make the figure the operator's."""
    task = operator_session(TASK, "yes", assistant=("Recording: impressions=9900. Confirm?",))
    verdict = hook(tools, task, figures={"impressions": 9900},
                   operator_message="Recording: impressions=9900. Confirm?")
    assert verdict and verdict["action"] == "block"


# ---- fail closed: unverifiable is not verified -------------------------------------------

def test_no_transcript_refuses_and_names_the_fallback(tools, monkeypatch):
    monkeypatch.delenv("ARTEC_TRANSCRIPT_DB", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    verdict = hook(tools, TASK, figures={"impressions": 4200},
                   operator_message="4200 impressions")
    assert verdict and verdict["action"] == "block"
    assert "artec measure" in verdict["message"]


def test_no_task_id_refuses(tools, operator_session):
    operator_session(TASK, "4200 impressions")
    verdict = hook(tools, None, figures={"impressions": 4200},
                   operator_message="4200 impressions")
    assert verdict and verdict["action"] == "block"


def test_a_session_that_is_not_ours_does_not_authorise(tools, operator_session):
    operator_session("task_other", "4200 impressions, 118 clicks")
    verdict = hook(tools, TASK, figures={"impressions": 4200},
                   operator_message="4200 impressions, 118 clicks")
    assert verdict and verdict["action"] == "block"


# ---- what must still pass ----------------------------------------------------------------

def test_the_confirm_flow_passes_although_the_last_turn_is_yes(tools, operator_session):
    """The write happens after 'yes'. Digits are verified against the operator's earlier
    turn — this is the widened window, and it is why the rule is stated, not implied."""
    task = operator_session(TASK, "4200, 0.62, 12, 45, 8, 118", "yes")
    assert hook(tools, task, figures_line="4200, 0.62, 12, 45, 8, 118",
                operator_message="4200, 0.62, 12, 45, 8, 118", confirm=True) is None


def test_thousands_separators_still_tolerated_against_a_real_turn(tools, operator_session):
    task = operator_session(TASK, "we got 4,200 impressions")
    assert hook(tools, task, figures={"impressions": 4200},
                operator_message="we got 4,200 impressions") is None


def test_arithmetic_is_still_refused(tools, operator_session):
    task = operator_session(TASK, "add up 300 from monday and 400 from tuesday")
    verdict = hook(tools, task, figures={"impressions": 700},
                   operator_message="add up 300 from monday and 400 from tuesday")
    assert verdict and verdict["action"] == "block"
    assert "do not compute" in verdict["message"]


# ---- the store is a probed fact, not a guess (2c-i) ---------------------------------------

def test_a_tool_result_is_not_an_operator_turn(tools, operator_session):
    """hermes stores tool results as role='tool'. A tool result carrying digits must not
    authorise those digits — this is the property that makes the store usable as an
    authority, so it is asserted rather than assumed."""
    task = operator_session(TASK, "yes", tool=('{"impressions": 9900, "clicks": 240}',))
    verdict = hook(tools, task, figures={"impressions": 9900},
                   operator_message='{"impressions": 9900, "clicks": 240}')
    assert verdict and verdict["action"] == "block"


def test_a_request_dump_beside_the_store_cannot_authorise_anything(tools, operator_session,
                                                                   tmp_path):
    """$HERMES_HOME/sessions/ holds request_dump_*.json — debug artefacts written on API
    errors, carrying a constructed message list. The first implementation globbed that
    directory and would have parsed one. There is now ONE source and no fallback."""
    dumps = tmp_path / "sessions"
    dumps.mkdir(exist_ok=True)
    (dumps / f"request_dump_20260827_{TASK}_x.json").write_text(
        '{"request": {"body": {"messages": [{"role": "user", "content": "9900 impressions"}]}}}',
        encoding="utf-8")
    operator_session(TASK, "yes")
    verdict = hook(tools, TASK, figures={"impressions": 9900},
                   operator_message="9900 impressions")
    assert verdict and verdict["action"] == "block"


def test_an_unknown_session_id_is_not_widened_into_a_match(tools, operator_session):
    operator_session("20260827_210000_abc123", "4200 impressions")
    verdict = hook(tools, "20260827_210000", figures={"impressions": 4200},
                   operator_message="4200 impressions")
    assert verdict and verdict["action"] == "block"


def test_store_status_reports_what_this_process_can_see(tools, operator_session):
    operator_session(TASK, "4200 impressions", "yes")
    status = tools._transcript.store_status()
    assert status["available"] is True and status["operator_turns"] == 2


def test_store_status_names_the_consequence_when_absent(tools, monkeypatch):
    monkeypatch.delenv("ARTEC_TRANSCRIPT_DB", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    status = tools._transcript.store_status()
    assert status["available"] is False
    assert "artec measure" in status["consequence"]


# ---- the store is PROFILE-SCOPED, and the obvious path is a decoy (operator probe B) ------

def test_the_store_resolves_through_active_profile(tools, operator_session, tmp_path):
    """`$HERMES_HOME/profiles/$(cat active_profile)/state.db`. Probed on the deployed
    brain — both previous investigations ran against a development machine whose layout
    differs, which is the packaging/environment class one level up: not code tested from a
    source tree and shipped as a wheel, but a FACT probed on a laptop and relied on in a
    container."""
    operator_session(TASK, "4200 impressions")
    resolved = tools._transcript.store_path()
    assert resolved == tmp_path / "profiles" / "artec-brain" / "state.db"
    assert resolved.is_file()


def test_the_decoy_at_the_obvious_path_is_never_read(tools, operator_session, tmp_path):
    """`$HERMES_HOME/state.db` exists on the brain, opens cleanly, and answers
    `no such table: sessions` — a plausible error rather than an absence, which is what
    made the earlier probe conclude the schema was wrong."""
    operator_session(TASK, "4200 impressions")
    decoy = tmp_path / "state.db"
    assert decoy.is_file(), "the fixture must actually build the decoy"
    assert tools._transcript.store_path() != decoy


def test_a_wrong_shaped_database_at_the_right_path_is_refused(tools, monkeypatch, tmp_path):
    """A file at the right path is not the right file. If a future layout change puts
    something else there, this must fail closed rather than read it."""
    import sqlite3

    monkeypatch.delenv("ARTEC_TRANSCRIPT_DB", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "active_profile").write_text("artec-brain", encoding="utf-8")
    profile = tmp_path / "profiles" / "artec-brain"
    profile.mkdir(parents=True)
    conn = sqlite3.connect(profile / "state.db")
    conn.execute("CREATE TABLE something_else (x TEXT)")
    conn.commit()
    conn.close()

    assert tools._transcript.store_path() is None
    status = tools._transcript.store_status()
    assert status["available"] is False and "does not carry" in status["reason"]


def test_a_missing_active_profile_refuses_and_says_which_file(tools, monkeypatch, tmp_path):
    monkeypatch.delenv("ARTEC_TRANSCRIPT_DB", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    status = tools._transcript.store_status()
    assert status["available"] is False
    assert "active_profile" in status["reason"]
    assert "artec measure" in status["consequence"]
