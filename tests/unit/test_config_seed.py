"""`artec config seed` must never silently clobber operator-set values (seo_seeds once
died this way, discovered only when ideate refused to run)."""

from app.config import RUNTIME_KEYS, get_config, seed_config, set_config


def test_seed_preserves_operator_values_by_default(session):
    seeds = ["k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8", "k9", "k10"]
    set_config(session, "seo_seeds", seeds)
    result = seed_config(session)
    assert get_config(session, "seo_seeds") == seeds        # untouched
    assert "seo_seeds" in result["kept"]                    # and loudly reported
    assert result["overwritten"] == []


def test_seed_force_overwrites_and_reports(session):
    set_config(session, "seo_seeds", ["operator"])
    result = seed_config(session, force=True)
    assert get_config(session, "seo_seeds") == []           # shipped default restored
    assert "seo_seeds" in result["overwritten"]


def test_explicit_override_wins_without_force(session):
    set_config(session, "seo_seeds", ["old"])
    result = seed_config(session, overrides={"seo_seeds": ["from-file"]})
    assert get_config(session, "seo_seeds") == ["from-file"]  # --file key = operator intent
    assert "seo_seeds" in result["overwritten"]


def test_runtime_state_never_overwritten_even_with_force(session):
    # post_id_counter left config entirely in v4 Stage 2b — ids come from post_id_seq so
    # that no tool ever writes config. Runtime state that remains must still survive force.
    set_config(session, "drive_page_token", "tok-abc")
    set_config(session, "confirm_first_publish", False)
    seed_config(session, force=True)
    assert get_config(session, "drive_page_token") == "tok-abc"
    assert get_config(session, "confirm_first_publish") is False
    assert "drive_page_token" in RUNTIME_KEYS
    assert "post_id_counter" not in RUNTIME_KEYS
