"""v4 foundation — acceptance 17 (required config), 27 (person assets + taxonomy layers),
plus the price-table invariant that reconciliation never writes config."""

import pytest

from app.config import (
    OPERATOR_CONSTANTS,
    REMOVED_KEYS,
    REQUIRED_CONFIG_KEYS,
    RequiredConfigMissing,
    validate_required_config,
)
from app.models import Asset, Config
from app.taxonomy import path_to_tags
from app.toolbox.asset_match import find_candidates

# ---- acceptance 17: boot refuses to start, naming the key ------------------------------

def test_required_config_validates_when_seeded(session):
    for role in ("api", "scheduler"):
        keys = validate_required_config(session, role)
        assert "slot_times" in keys if role == "scheduler" else True


def test_boot_refuses_and_names_a_missing_key(session):
    # slot_times absent silently disabled the ENTIRE scheduler once. That class ends here.
    session.delete(session.get(Config, "slot_times"))
    session.flush()
    with pytest.raises(RequiredConfigMissing, match="slot_times"):
        validate_required_config(session, "scheduler")


def test_boot_refuses_on_an_empty_value_too(session):
    row = session.get(Config, "channel_media")
    row.value = {}
    session.flush()
    with pytest.raises(RequiredConfigMissing, match="channel_media"):
        validate_required_config(session, "api")


def test_seo_seeds_empty_is_not_boot_fatal(session):
    # ideate refuses loudly on empty seeds; the service must still boot to be fixable.
    row = session.get(Config, "seo_seeds")
    row.value = []
    session.flush()
    validate_required_config(session, "api")


def test_every_required_key_is_actually_seeded():
    # A manifest naming a key that config seed never writes would fail every boot.
    seeded = set(OPERATOR_CONSTANTS)
    for role, keys in REQUIRED_CONFIG_KEYS.items():
        missing = [k for k in keys if k not in seeded]
        assert not missing, f"role {role} requires unseeded keys: {missing}"


# ---- the price table left config, and must never come back -----------------------------

def test_price_table_is_not_in_config(session):
    assert "endpoint_prices_cents" not in OPERATOR_CONSTANTS
    assert "endpoint_prices_cents" in REMOVED_KEYS
    assert session.get(Config, "endpoint_prices_cents") is None


def test_seed_prices_is_idempotent_and_priced_in_micros(session):
    from app.models import EndpointPrice
    from app.toolbox.pricing import seed_prices

    seed_prices(session)
    first = {r.endpoint: r.rate_micros for r in session.query(EndpointPrice).all()}
    seed_prices(session)
    second = {r.endpoint: r.rate_micros for r in session.query(EndpointPrice).all()}
    assert first == second and len(first) == 4
    assert first["fal-ai/clarity-upscaler"] == 30_000       # $0.030 per megapixel


def test_v4_caps_are_the_new_values():
    assert OPERATOR_CONSTANTS["render_run_cap_cents"] == 250   # raised
    assert OPERATOR_CONSTANTS["per_call_ceiling_cents"] == 50  # deliberately NOT raised
    assert OPERATOR_CONSTANTS["max_output_megapixels"] == 4.0


# ---- acceptance 27: person assets + Layer1=medium / Layer2=subject ----------------------

def test_medium_comes_from_layer1_and_subject_from_layer2():
    # Never the reverse, and never guessed from a file extension.
    video = path_to_tags("raw-video/child-face")
    photo = path_to_tags("raw-photo/child-face")
    assert (video.medium, video.subject, video.has_person) == ("video", "child_face", True)
    assert (photo.medium, photo.subject, photo.has_person) == ("photo", "child_face", True)
    # same subject, different medium — decided purely by Layer 1
    assert video.subject == photo.subject and video.medium != photo.medium

    v_pc = path_to_tags("raw-video/parent-child")
    p_pc = path_to_tags("raw-photo/parent-child")
    assert (v_pc.medium, v_pc.subject, v_pc.has_person) == ("video", "parent_child", True)
    assert (p_pc.medium, p_pc.subject, p_pc.has_person) == ("photo", "parent_child", True)


def test_all_four_face_folders_carry_has_person():
    for folder in ("raw-photo/parent-child", "raw-photo/child-face",
                   "raw-video/parent-child", "raw-video/child-face"):
        assert path_to_tags(folder).has_person is True, folder


def test_person_assets_now_appear_in_matching(session):
    # v4 flips allow_person_assets to true — the 202 gated assets join the pool.
    assert OPERATOR_CONSTANTS["allow_person_assets"] is True
    session.add(Asset(drive_file_id="face1", drive_path="raw-video/child-face/a.mp4",
                      layer1="raw-video", layer2="child-face", medium="video",
                      subject="child_face", has_person=True, aspect="vertical",
                      status="active"))
    session.flush()
    ids = {a.drive_file_id for a in find_candidates(session, "child_face", "video",
                                                    allow_person=True)}
    assert "face1" in ids
    # and the gate still works when the flag is off
    assert find_candidates(session, "child_face", "video", allow_person=False) == []
