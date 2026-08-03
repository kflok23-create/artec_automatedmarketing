"""v4 2c-i — A6·2 spend posture, A5 scouting probe, A4 memory audit, C6 toolset drift.

The load-bearing claim in all four: a degradation, a probe failure or a rename must be
VISIBLE, and the gate must survive all of them.
"""

import importlib.util

import httpx
import pytest
import respx

from app.stages.agent_review import EXPECTED_DISABLED, toolset_drift_check


@pytest.fixture
def agent_runs(repo_root):
    spec = importlib.util.spec_from_file_location(
        "spend_agent_runs", repo_root / "plugins" / "artec" / "agent_runs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe(repo_root):
    spec = importlib.util.spec_from_file_location(
        "probe_scouting", repo_root / "deploy" / "hermes-brain" / "probe_scouting.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def audit(repo_root):
    spec = importlib.util.spec_from_file_location(
        "audit_memory_report", repo_root / "deploy" / "hermes-brain" / "audit_memory_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- A6·2 · the degradation ORDER --------------------------------------------------------

def test_normal_spend_keeps_everything(agent_runs):
    p = agent_runs.spend_posture(300, 1500)          # 20%
    assert p["scouting"] is True and p["gate_style"] == "full" and not p["degraded"]


def test_scouting_is_the_first_thing_dropped(agent_runs):
    p = agent_runs.spend_posture(900, 1500)          # 60%
    assert p["scouting"] is False
    assert p["gate_style"] == "full", "the gate conversation is not shortened first"
    assert "scouting dropped" in p["actions"][0]


def test_the_gate_conversation_shortens_second(agent_runs):
    p = agent_runs.spend_posture(1300, 1500)         # 87%
    assert p["scouting"] is False and p["gate_style"] == "short"


def test_at_and_past_the_cap_scouting_is_gone_and_the_gate_still_runs(agent_runs):
    for spent in (1500, 1501, 3000, 100_000):
        p = agent_runs.spend_posture(spent, 1500)
        assert p["scouting"] is False
        assert p["gate_runs"] is True, "the gate is never traded for headroom"
        assert p["gate_style"] == "short"


@pytest.mark.parametrize("spent", [0, 1, 149, 899, 900, 1274, 1275, 1499, 1500, 9_999_999])
def test_no_spend_level_produces_a_posture_without_a_gate(agent_runs, spent):
    """Property, not a threshold check: 'never skip the gate' must not depend on getting a
    boundary right."""
    assert agent_runs.spend_posture(spent, 1500)["gate_runs"] is True


def test_a_zero_or_missing_cap_does_not_silently_disable_the_gate(agent_runs):
    for cap in (0, None):
        p = agent_runs.spend_posture(500, cap)
        assert p["gate_runs"] is True


def test_the_posture_reaches_the_brain_on_the_read_it_makes_first(session, repo_root, engine):
    from app.models import AgentRun

    spec = importlib.util.spec_from_file_location(
        "posture_tools", repo_root / "plugins" / "artec" / "tools.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)

    from datetime import UTC, datetime

    session.add(AgentRun(job="learn-ideate", started_at=datetime.now(UTC), status="ok",
                         cost_cents=1400))            # 93% of the 1500c cap
    session.commit()

    brief = tools._read_brief_impl(engine=engine)
    assert "AGENT SPEND POSTURE" in brief
    assert "scouting: DROPPED" in brief
    assert "gate: RUNS" in brief
    assert "never skipped" in brief


# ---- A5 · the probe hits the real endpoint and gates on the response ----------------------

@respx.mock
def test_a_200_with_results_is_available(probe):
    respx.post(probe.TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": [{"title": "x"}]}))
    status = probe.probe_tavily("tvly-fake")
    assert status["available"] is True and "1 result" in status["reason"]


@respx.mock
def test_a_401_is_unavailable_and_carries_the_backend_reason(probe):
    """Presence is not validity — this project lost a cycle to a key that passed a
    presence check and 401'd at first use."""
    respx.post(probe.TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(401, text="Unauthorized: invalid API key"))
    status = probe.probe_tavily("tvly-wrong")
    assert status["available"] is False
    assert "401" in status["reason"] and "invalid API key" in status["reason"]


@respx.mock
def test_a_200_with_zero_results_is_not_a_working_backend(probe):
    respx.post(probe.TAVILY_SEARCH_URL).mock(return_value=httpx.Response(200, json={"results": []}))
    assert probe.probe_tavily("tvly-fake")["available"] is False


def test_an_absent_key_is_reported_not_assumed(probe):
    status = probe.probe_tavily("")
    assert status["available"] is False and "not set" in status["reason"]


@respx.mock
def test_a_network_failure_never_raises(probe):
    respx.post(probe.TAVILY_SEARCH_URL).mock(side_effect=httpx.ConnectTimeout("x"))
    status = probe.probe_tavily("tvly-fake")
    assert status["available"] is False and "ConnectTimeout" in status["reason"]


@respx.mock
def test_the_probe_never_puts_the_key_in_its_own_status(probe):
    """The status is written to config and rendered in the digest — a key must not ride
    along into either."""
    respx.post(probe.TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(403, text="forbidden"))
    status = probe.probe_tavily("tvly-SUPERSECRET-0001")
    assert "SUPERSECRET" not in str(status)


def test_the_digest_reports_the_probe_result_nightly(session):
    from app.config import set_config
    from app.stages.digest import build_payload, render_digest_text

    set_config(session, "scouting_status",
               {"available": False, "backend": "tavily",
                "reason": "HTTP 401: Unauthorized: invalid API key"})

    class Brevo:
        def get_list_count(self):
            return 1

    text = render_digest_text(build_payload(session, brevo=Brevo()))
    assert "scouting: UNAVAILABLE" in text and "401" in text


# ---- A4 · autonomous memory writes are observed from the first week ----------------------

def test_the_audit_is_clean_on_empty_memory(audit, tmp_path):
    (tmp_path / "skills").mkdir()
    result = audit.scan(tmp_path)
    assert result["clean"] is True and result["hits"] == []


def test_the_audit_catches_a_number_that_reached_memory(audit, tmp_path):
    (tmp_path / "MEMORY.md").write_text(
        "Playbook: hooks that name a time work well.\n"
        "2026-08-27 carousels: 4200 impressions.\n"
        "CAC was RM45.00 last week.\n", encoding="utf-8")
    result = audit.scan(tmp_path)
    assert result["clean"] is False
    kinds = {h["kind"] for h in result["hits"]}
    assert "currency amount" in kinds and "date-stamped count" in kinds


def test_the_audit_patterns_do_not_drift_from_the_cli_version(audit):
    """The brain image has no app package, so the patterns are duplicated. Duplicated is
    fine; SILENTLY DIVERGENT is not."""
    from app.stages.agent_review import METRIC_PATTERNS

    assert [label for label, _ in METRIC_PATTERNS] == [label for label, _ in audit.METRIC_PATTERNS]
    assert [p.pattern for _, p in METRIC_PATTERNS] == [p for _, p in audit.METRIC_PATTERNS]


def test_the_digest_reports_the_audit_and_says_so_when_it_has_never_run(session):
    from app.config import set_config
    from app.stages.digest import build_payload, render_digest_text

    class Brevo:
        def get_list_count(self):
            return 1

    assert "NOT YET RUN" in render_digest_text(build_payload(session, brevo=Brevo()))

    set_config(session, "memory_audit", {"clean": False, "scanned_files": 3, "hits": [
        {"file": "/data/hermes/MEMORY.md", "line": 2, "kind": "currency amount",
         "match": "RM45.00"}]})
    text = render_digest_text(build_payload(session, brevo=Brevo()))
    assert "agent memory audit: 1 metric-shaped hit" in text
    assert "RM45.00" in text


# ---- C6 · identifier drift ----------------------------------------------------------------

def test_drift_check_passes_when_every_identifier_is_recognised():
    result = toolset_drift_check(runner=lambda _: "\n".join(EXPECTED_DISABLED),
                                 log=lambda *_: None)
    assert result["checked"] is True and result["missing"] == []


def test_drift_check_is_red_when_an_identifier_disappears():
    names = [n for n in EXPECTED_DISABLED if n not in ("tts", "bfl")]
    result = toolset_drift_check(runner=lambda _: "\n".join(names), log=lambda *_: None)
    assert sorted(result["missing"]) == ["bfl", "tts"]


def test_an_alias_still_present_covers_the_capability():
    """`kanban` → `todo` is the real rename. Listing both is why that one did not silently
    re-enable a board, and the check must not shout about the retired half."""
    names = [n for n in EXPECTED_DISABLED if n != "kanban"]
    result = toolset_drift_check(runner=lambda _: "\n".join(names), log=lambda *_: None)
    assert "kanban" not in result["missing"]


def test_hermes_absent_is_a_skip_never_a_pass():
    result = toolset_drift_check(runner=lambda _: (_ for _ in ()).throw(FileNotFoundError()),
                                 log=lambda *_: None)
    assert result["checked"] is False and result["missing"] == []
