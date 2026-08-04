"""v4 2c-iii — price reconciliation (job 1's first action) and `artec prove`.

Both exist because a number nobody re-checks drifts, and a capability nobody exercises is an
intention with code attached.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import EndpointPrice
from app.stages import prove as prove_mod
from app.toolbox.pricing import UNIT_PER_IMAGE, UNIT_PER_MEGAPIXEL
from app.toolbox.reconcile import calls_at_cap, is_material, pull_rates, reconcile_prices

CAP = 250


class _Resp:
    def __init__(self, status=200, payload=None, text="ok"):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    def __init__(self, response=None, boom=None):
        self._response = response
        self._boom = boom

    def get(self, url, timeout=None):
        if self._boom:
            raise self._boom
        return self._response


# ---- what the cap MEANS ------------------------------------------------------------------

def test_calls_at_cap_is_the_number_the_cap_actually_means():
    # 30_000 micros/MP x 2.0736 MP = 62_208 micros = 6.22c; 250c affords 40
    assert calls_at_cap(30_000, UNIT_PER_MEGAPIXEL, CAP) == 40
    assert calls_at_cap(40_000, UNIT_PER_IMAGE, CAP) == 62


def test_a_delta_that_changes_how_many_renders_the_cap_affords_is_MATERIAL():
    assert is_material(30_000, 45_000, UNIT_PER_MEGAPIXEL, CAP) is True


def test_a_delta_that_leaves_the_cap_meaning_the_same_is_not():
    assert is_material(30_000, 30_100, UNIT_PER_MEGAPIXEL, CAP) is False


# ---- the pull is PROBED, never assumed ---------------------------------------------------

def test_an_unreachable_pricing_endpoint_is_reported_not_guessed():
    rates, source = pull_rates(client=_Client(boom=RuntimeError("dns")))
    assert rates == {} and "unreachable" in source


def test_a_non_200_is_reported_with_its_status():
    rates, source = pull_rates(client=_Client(_Resp(503)))
    assert rates == {} and "HTTP 503" in source


def test_a_200_in_an_unexpected_shape_says_so_rather_than_parsing_nothing_quietly():
    rates, source = pull_rates(client=_Client(_Resp(200, {"data": "surprise"})))
    assert rates == {}
    assert "not the one this parser expects" in source


def test_a_recognisable_shape_parses_to_micros():
    rates, _ = pull_rates(client=_Client(_Resp(200, {"endpoints": [
        {"endpoint": "fal-ai/clarity-upscaler", "price_usd": 0.031}]})))
    assert rates == {"fal-ai/clarity-upscaler": 31_000}


# ---- reconciliation ----------------------------------------------------------------------

def test_a_failed_pull_flags_stale_and_does_not_fail_job_1(session):
    result = reconcile_prices(session, CAP, client=_Client(_Resp(500)), log=lambda *_: None)
    assert result.pulled is False
    assert "fal-ai/clarity-upscaler" in result.stale
    assert result.applied == [] and result.needs_acknowledgement == []


def test_a_small_delta_applies_and_is_reported(session):
    result = reconcile_prices(session, CAP, client=_Client(_Resp(200, {"endpoints": [
        {"endpoint": "fal-ai/clarity-upscaler", "price_usd": 0.0301}]})), log=lambda *_: None)
    assert [d["endpoint"] for d in result.applied] == ["fal-ai/clarity-upscaler"]
    session.expire_all()
    row = session.get(EndpointPrice, "fal-ai/clarity-upscaler")
    assert row.rate_micros == 30_100 and row.source == "fal_api"


def test_a_material_delta_is_HELD_for_acknowledgement_and_not_applied(session):
    """The cap would silently come to mean something other than what was intended."""
    before = session.get(EndpointPrice, "fal-ai/clarity-upscaler").rate_micros
    result = reconcile_prices(session, CAP, client=_Client(_Resp(200, {"endpoints": [
        {"endpoint": "fal-ai/clarity-upscaler", "price_usd": 0.045}]})), log=lambda *_: None)
    assert result.applied == []
    held = result.needs_acknowledgement[0]
    assert held["calls_at_cap_before"] == 40 and held["calls_at_cap_after"] < 40
    session.expire_all()
    assert session.get(EndpointPrice, "fal-ai/clarity-upscaler").rate_micros == before


def test_reconciliation_never_writes_config(session):
    """The no-config-writes rule is not weakened to make this convenient."""
    from app.models import Config

    before = {c.key: c.value for c in session.query(Config).all()}
    reconcile_prices(session, CAP, client=_Client(_Resp(200, {"endpoints": [
        {"endpoint": "fal-ai/clarity-upscaler", "price_usd": 0.0301}]})), log=lambda *_: None)
    session.expire_all()
    assert {c.key: c.value for c in session.query(Config).all()} == before


def test_an_endpoint_fal_did_not_price_is_stale_not_zero(session):
    result = reconcile_prices(session, CAP, client=_Client(_Resp(200, {"endpoints": [
        {"endpoint": "fal-ai/clarity-upscaler", "price_usd": 0.030}]})), log=lambda *_: None)
    assert "fal-ai/flux-pro/kontext" in result.stale
    session.expire_all()
    assert session.get(EndpointPrice, "fal-ai/flux-pro/kontext").rate_micros == 40_000


def test_inactive_endpoints_stay_priced_so_a_re_enable_arrives_priced(session):
    row = session.get(EndpointPrice, "fal-ai/qwen-image-2512/lora")
    assert row.active is False and row.rate_micros > 0


# ---- prove -------------------------------------------------------------------------------

def test_the_nine_capabilities_are_all_provable_by_name():
    assert set(prove_mod.PROVERS) == set(prove_mod.CAPABILITIES)
    assert len(prove_mod.CAPABILITIES) == 9
    assert set(prove_mod.S1) == {"video-pipeline", "publish-by-slot"}


def test_nothing_is_proven_until_it_runs(session):
    rows = prove_mod.proof_status(session)
    assert {r["state"] for r in rows} == {"never"}
    assert len(prove_mod.unproven(session)) == 9


def test_budget_refusal_proves_against_real_code(session):
    proof = prove_mod.run(session, "budget-refusal", log=lambda *_: None)
    assert proof.ok is True
    assert proof.evidence["one_call_micros"] == 62_208
    assert [r for r in prove_mod.proof_status(session)
            if r["capability"] == "budget-refusal"][0]["state"] == "proven"


def test_a_stale_proof_is_not_a_proof(session):
    from app.config import set_config

    old = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    set_config(session, "proofs", {"budget-refusal": {"ok": True, "detail": "x", "at": old}})
    row = [r for r in prove_mod.proof_status(session)
           if r["capability"] == "budget-refusal"][0]
    assert row["state"] == "stale"


def test_a_recorded_failure_stays_failed(session):
    from app.config import set_config

    set_config(session, "proofs", {"restore": {
        "ok": False, "detail": "CREATE DATABASE denied",
        "at": datetime.now(UTC).isoformat()}})
    row = [r for r in prove_mod.proof_status(session) if r["capability"] == "restore"][0]
    assert row["state"] == "failed"


def test_not_provable_is_a_third_state_never_a_pass(session, monkeypatch):
    """Recording a skip as a proof is how a capability comes to be believed without ever
    having run."""
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with pytest.raises(prove_mod.NotProvable):
        prove_mod.run(session, "audit-memory", log=lambda *_: None)
    assert [r for r in prove_mod.proof_status(session)
            if r["capability"] == "audit-memory"][0]["state"] == "never"


def test_restore_without_a_dump_refuses_rather_than_proving_nothing(session):
    with pytest.raises(prove_mod.NotProvable, match="proving nothing"):
        prove_mod.run(session, "restore", log=lambda *_: None)


def test_brevo_live_is_refused_in_this_pass(session):
    with pytest.raises(prove_mod.NotProvable, match="deliberate operator action"):
        prove_mod.run(session, "brevo-send", live=True, log=lambda *_: None)


def test_publish_by_slot_over_an_empty_board_is_NOT_proven(session):
    """S1. Proving "the loop ran without error" over an empty set would sign off the first
    unattended action this system takes against nothing. Found by running it."""
    proof = prove_mod.run(session, "publish-by-slot", log=lambda *_: None)
    assert proof.ok is False
    assert "not that it selects correctly" in proof.detail
    assert set(proof.evidence["by_slot"]) == {"morning", "lunch", "evening", "weekend"}


def test_publish_by_slot_is_proven_once_there_is_something_to_select(session):
    from datetime import date

    from app.models import Post

    session.add(Post(post_id="post_prove_1", week_start=date(2026, 8, 24),
                     channel="instagram", status="RENDERED", slot="evening",
                     media_drive_file_id="gen_1.jpg"))
    session.flush()
    proof = prove_mod.run(session, "publish-by-slot", log=lambda *_: None)
    assert proof.ok is True and proof.evidence["would_publish"] == ["post_prove_1"]


def test_a_prover_that_raises_is_recorded_as_a_FAILURE_not_a_crash(session, monkeypatch):
    """Found by running it: an uncaught error aborted the run and left the rest of the
    matrix unexamined."""
    monkeypatch.setitem(prove_mod.PROVERS, "budget-refusal",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    proof = prove_mod.run(session, "budget-refusal", log=lambda *_: None)
    assert proof.ok is False and "RuntimeError: boom" in proof.detail


def test_the_digest_lists_unproven_capabilities(session):
    from app.stages.digest import build_payload, render_digest_text

    class Brevo:
        def get_list_count(self):
            return 1

    text = render_digest_text(build_payload(session, brevo=Brevo()))
    assert "unproven capabilities (9)" in text
    assert "S1 UNPROVEN: video-pipeline, publish-by-slot" in text


def test_doctor_reports_unproven_as_yellow_never_green(session):
    from app.settings import get_settings
    from app.stages.doctor import run_doctor

    checks = run_doctor(get_settings(), session=session)
    proof_check = [c for c in checks if c.name == "capability proofs"][0]
    assert proof_check.warn is True and proof_check.ok is True
    assert "unproven or stale" in proof_check.detail


def test_audit_memory_refuses_a_hermes_install_that_is_not_artecs_brain(session, tmp_path,
                                                                        monkeypatch):
    """Found by running it: on a development machine HERMES_HOME points at a PERSONAL
    hermes install, and the audit reported 568 hits in somebody else's notes — a confident
    FAILED about the wrong memory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "MEMORY.md").write_text("CAC was RM45.00\n", encoding="utf-8")
    with pytest.raises(prove_mod.NotProvable, match="not artec's brain"):
        prove_mod.run(session, "audit-memory", log=lambda *_: None)

    (tmp_path / "plugins" / "artec").mkdir(parents=True)
    proof = prove_mod.run(session, "audit-memory", log=lambda *_: None)
    assert proof.ok is False, "now it is artec's brain, and the hit is real"
