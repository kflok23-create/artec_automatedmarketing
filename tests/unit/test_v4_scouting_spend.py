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
    assert "agent memory audit: 1 currency amount" in text
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


# ---- C · scouting has THREE states; absent is not passing ---------------------------------

class _Brevo:
    def get_list_count(self):
        return 1


def _digest_text(session):
    from app.stages.digest import build_payload, render_digest_text

    return render_digest_text(build_payload(session, brevo=_Brevo()))


def test_scouting_absent_renders_as_not_yet_probed(session):
    """The probe's status write failed once (postgres.railway.internal does not resolve
    outside Railway), which leaves the key ABSENT rather than wrong. An absent probe must
    never read as a passing one, and must never be silently omitted."""
    text = _digest_text(session)
    assert "scouting: NOT YET PROBED" in text
    assert "not a passing one" in text


def test_scouting_available_renders_the_backend_and_reason(session):
    from app.config import set_config

    set_config(session, "scouting_status",
               {"available": True, "backend": "tavily",
                "reason": "HTTP 200, 1 result(s) for the probe query"})
    text = _digest_text(session)
    assert "scouting: available via tavily" in text and "HTTP 200" in text


def test_scouting_unavailable_renders_the_reason(session):
    from app.config import set_config

    set_config(session, "scouting_status",
               {"available": False, "backend": "tavily", "reason": "HTTP 401: Unauthorized"})
    text = _digest_text(session)
    assert "scouting: UNAVAILABLE" in text and "401" in text


# ---- F2 · memory that contradicts the build, and memory that gives orders -----------------

def _memory_home(tmp_path, text: str, tools: int = 15):
    (tmp_path / "plugins" / "artec").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plugins" / "artec" / "plugin.yaml").write_text(
        "provides_tools:\n" + "\n".join(f"  - tool_{i}" for i in range(tools))
        + "\nprovides_hooks:\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(text, encoding="utf-8")
    return tmp_path


LIVE_MEMORY = (
    "artec plugin exposes exactly 6 tools. There is NO tool_3 tool. "
    "Don't promise those; say so plainly.\n")


def test_a_stale_tool_count_is_caught(audit, tmp_path):
    """THE LIVE CASE. Memory asserted six tools while the seam had fifteen — and memory is
    injected into every turn, so the next gate would have been told, with authority, that
    capabilities the agent holds do not exist."""
    result = audit.scan(_memory_home(tmp_path, LIVE_MEMORY))
    stale = [h for h in result["hits"] if h["kind"] == "stale capability claim"]
    assert stale, result["hits"]
    assert any("says 6 tools" in h["detail"] and "has 15" in h["detail"] for h in stale)


def test_denying_a_tool_that_exists_is_caught(audit, tmp_path):
    result = audit.scan(_memory_home(tmp_path, LIVE_MEMORY))
    assert any("denies tools that EXIST" in h.get("detail", "") for h in result["hits"])


def test_an_imperative_in_memory_is_caught(audit, tmp_path):
    """'Don't promise those; say so plainly' is not a fact — it is a standing instruction
    re-read in every later session."""
    result = audit.scan(_memory_home(tmp_path, LIVE_MEMORY))
    imperatives = [h for h in result["hits"] if h["kind"] == "imperative in memory"]
    assert {h["match"].lower() for h in imperatives} >= {"don't", "say so"}


def test_declarative_playbook_memory_stays_clean(audit, tmp_path):
    result = audit.scan(_memory_home(
        tmp_path, "Hooks that name a time outperform hooks that name a feeling.\n"
                  "Parents respond to finished builds more than to loose blocks.\n"))
    assert result["clean"] is True, result["hits"]


def test_an_unknown_registry_never_accuses(audit, tmp_path):
    """No plugin.yaml on the volume = unknown registry. Unknown must not manufacture a
    contradiction — it can only fail to detect one."""
    (tmp_path / "MEMORY.md").write_text("artec exposes exactly 6 tools.\n", encoding="utf-8")
    result = audit.scan(tmp_path)
    assert not [h for h in result["hits"] if h["kind"] == "stale capability claim"]


def test_memory_utilisation_is_reported(audit, tmp_path):
    result = audit.scan(_memory_home(tmp_path, "x" * 1958))       # 89% of 2200
    assert result["memory_cap"] == 2200
    assert result["memory_utilisation"] == 0.89


def test_the_digest_warns_before_a_silent_eviction(session):
    from app.config import set_config
    from app.stages.digest import build_payload, render_digest_text

    class Brevo:
        def get_list_count(self):
            return 1

    set_config(session, "memory_audit", {
        "clean": True, "scanned_files": 2, "memory_chars": 1958, "memory_cap": 2200,
        "memory_utilisation": 0.89})
    text = render_digest_text(build_payload(session, brevo=Brevo()))
    assert "MEMORY 89% of 2200 chars" in text
    assert "silently evicts an old one" in text


def test_the_capability_patterns_do_not_drift_from_the_cli_version(audit):
    from app.stages.agent_review import CAPABILITY_PATTERNS, MEMORY_CAP_CHARS, WORD_NUMBERS

    assert MEMORY_CAP_CHARS == audit.MEMORY_CAP_CHARS
    assert WORD_NUMBERS == audit.WORD_NUMBERS
    live = [audit.TOOL_COUNT_RE.pattern, audit.NO_SUCH_TOOL_RE.pattern,
            audit.IMPERATIVE_RE.pattern]
    assert [p for _, p in CAPABILITY_PATTERNS] == live


# ---- D2 · the merge gate is a written rule, so it has to be VISIBLE ----------------------

from app.stages.agent_review import main_ci_gate_check  # noqa: E402


def _api(sha="a1fa8fd0000000000000000000000000000000ab", runs=None):
    def fetch(url: str):
        if url.endswith("/commits/main"):
            return {"sha": sha}
        return {"workflow_runs": runs if runs is not None else []}
    return fetch


def test_a_green_run_on_mains_head_is_the_gate_holding():
    result = main_ci_gate_check(
        fetch=_api(runs=[{"id": 30872795253, "conclusion": "success"}]),
        log=lambda *_: None)
    assert result["checked"] is True and result["green"] is True
    assert result["run_id"] == 30872795253


def test_a_failed_run_is_red():
    result = main_ci_gate_check(
        fetch=_api(runs=[{"id": 1, "conclusion": "failure"}]), log=lambda *_: None)
    assert result["checked"] is True and result["green"] is False


def test_a_commit_with_no_ci_run_at_all_is_red():
    """THE case the rule exists to catch: branch protection cannot stop a merge on this
    plan, so a commit CI never saw is exactly what would slip through."""
    messages = []
    result = main_ci_gate_check(fetch=_api(runs=[]), log=messages.append)
    assert result["checked"] is True and result["green"] is False
    assert "NO CI run" in " ".join(messages)


def test_no_token_is_NOT_CHECKED_and_never_a_pass(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    messages = []
    result = main_ci_gate_check(log=messages.append)
    assert result["checked"] is False and result["green"] is False
    assert "NOT CHECKED" in " ".join(messages)
    assert "written rule still stands" in " ".join(messages)


def test_an_api_failure_is_not_checked_rather_than_green():
    def boom(url):
        raise RuntimeError("network down")

    result = main_ci_gate_check(fetch=boom, log=lambda *_: None)
    assert result["checked"] is False and result["green"] is False
