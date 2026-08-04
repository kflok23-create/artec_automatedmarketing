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


# ---- v4 §0.3 — supersession may correct a shipped default, never an operator's choice ----

def test_a_superseded_default_is_upgraded_only_when_the_seeder_wrote_it(session):
    from app.config import seed_config
    from app.models import Config

    row = session.get(Config, "agent_weekly_cap_minor")
    assert row.set_by == "seed" and row.value == 1500
    row.value = 500                                        # as an older seed left it
    session.flush()

    result = seed_config(session)
    assert "agent_weekly_cap_minor" in result["upgraded"]
    assert session.get(Config, "agent_weekly_cap_minor").value == 1500


def test_an_operator_choice_is_never_superseded(session):
    from app.config import seed_config, set_config
    from app.models import Config

    set_config(session, "agent_weekly_cap_minor", 500, set_by="operator")
    result = seed_config(session)
    assert "agent_weekly_cap_minor" not in result["upgraded"]
    assert session.get(Config, "agent_weekly_cap_minor").value == 500
    assert "agent_weekly_cap_minor" in result["kept"]


def test_unknown_provenance_is_reported_not_overwritten(session):
    """Rows written before provenance existed cannot be told from a deliberate choice.
    Silently taking the new default there would be config-silence inside the mechanism
    built to prevent it — so it is surfaced and the operator decides."""
    from app.config import seed_config
    from app.models import Config

    row = session.get(Config, "agent_weekly_cap_minor")
    row.value, row.set_by = 500, None
    session.flush()

    result = seed_config(session)
    assert result["upgraded"] == []
    assert session.get(Config, "agent_weekly_cap_minor").value == 500
    assert any("agent_weekly_cap_minor" in line for line in result["needs_decision"])
    assert any("artec config set" in line for line in result["needs_decision"])
