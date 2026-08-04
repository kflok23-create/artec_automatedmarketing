"""Acceptance 8, 9 (asset matching), 10 (v_brief cap), plus selector bank-first rules."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from app.models import Asset, Learning, Post
from app.toolbox.asset_match import find_candidates, mark_used
from app.toolbox.selector import PlanError, fallback_plan, validate_plan


def _asset(i, subject="assembled_blocks", medium="photo", has_person=False, aspect="square",
           times_used=0, last_used_at=None, status="active"):
    return Asset(drive_file_id=f"a{i}", drive_path=f"p/{i}.jpg", medium=medium, subject=subject,
                 has_person=has_person, aspect=aspect, times_used=times_used,
                 last_used_at=last_used_at, status=status)


def test_person_assets_excluded_when_gated(session):
    session.add_all([_asset(1, has_person=True), _asset(2, has_person=False), _asset(3, has_person=None)])
    session.flush()
    ids = {a.drive_file_id for a in find_candidates(session, "assembled_blocks", "photo",
                                                    allow_person=False)}
    assert "a1" not in ids            # 8: has_person=true gated off
    assert {"a2", "a3"} <= ids        # unknown (UGC-style) stays eligible per spec letter
    ids_open = {a.drive_file_id for a in find_candidates(session, "assembled_blocks", "photo",
                                                         allow_person=True)}
    assert "a1" in ids_open


def test_lru_preference_and_times_used_increment(session):
    session.add_all([
        _asset(1, times_used=5, last_used_at=datetime(2026, 7, 1, tzinfo=UTC)),
        _asset(2, times_used=0, last_used_at=None),  # never used → wins
        _asset(3, times_used=1, last_used_at=datetime(2026, 6, 1, tzinfo=UTC)),
    ])
    session.flush()
    candidates = find_candidates(session, "assembled_blocks", "photo")
    assert candidates[0].drive_file_id == "a2"   # 9: least-recently-used first
    mark_used(session, candidates[0])
    assert session.get(Asset, "a2").times_used == 1
    assert session.get(Asset, "a2").last_used_at is not None


def test_missing_assets_never_match(session):
    session.add(_asset(1, status="missing"))
    session.flush()
    assert find_candidates(session, "assembled_blocks", "photo") == []


def test_v_brief_capped(session):
    # 10: seed far more raw material than the cap and assert the LIMIT holds.
    for i in range(60):
        session.add(Post(post_id=f"post_{2000 + i}", week_start=date(2026, 7, 27),
                         channel="instagram", status="PUBLISHED", angle=f"angle{i}"))
        session.add(Learning(week_start=date(2026, 7, 27), lever="hook", lever_value=f"h{i}",
                             kpi="weighted", verdict="keep"))
        session.add(Asset(drive_file_id=f"b{i}", drive_path=f"raw-photo/{i}.jpg",
                          medium="photo", subject=f"subject{i % 20}", status="active"))
    session.flush()
    rows = session.execute(text("SELECT section, line FROM v_brief")).all()
    assert 0 < len(rows) <= 70


def test_selector_generate_is_not_a_tool(session):
    # v3: 'generate' is not in the routing vocabulary at all — unknown, not gated.
    candidates = [_asset(1)]
    with pytest.raises(PlanError, match="unknown tool"):
        validate_plan({"subject": "assembled_blocks", "tools": ["generate"], "asset_ids": [],
                       "prompt": "x"}, candidates, allow_person=False)


def test_selector_bank_only_for_product():
    with pytest.raises(PlanError, match="BANK-ONLY"):
        validate_plan({"subject": "assembled_blocks", "tools": ["overlay"], "asset_ids": [],
                       "prompt": ""}, [], allow_person=False)


def test_selector_rejects_foreign_asset_ids_and_gated_person():
    candidates = [_asset(1), _asset(2, has_person=True)]
    with pytest.raises(PlanError, match="not in the offered"):
        validate_plan({"subject": "assembled_blocks", "tools": ["asset"], "asset_ids": ["zz"]},
                      candidates, allow_person=False)
    with pytest.raises(PlanError, match="gated off"):
        validate_plan({"subject": "assembled_blocks", "tools": ["asset"], "asset_ids": ["a2"]},
                      candidates, allow_person=False)


def test_fallback_plan_bank_only_never_generates():
    photo = fallback_plan("assembled_blocks", [_asset(1)], "photo")
    assert photo.tools == ["asset", "enhance"] and photo.asset_ids == ["a1"]
    video = fallback_plan("assembled_blocks", [_asset(1, medium="video")], "video")
    assert video.tools == ["asset", "video_edit"]
    # v3 acceptance 11: product-depicting with an empty bank → None → PARK, never generate.
    assert fallback_plan("assembled_blocks", [], "photo") is None
    assert fallback_plan("child_face", [], "video") is None
    # Product-free message post — the zero-asset brand card survives.
    assert fallback_plan("brand_message", [], "photo").tools == ["text_card"]


# ---- F3 · the defect the AGENT found in production and wrote into its own memory ---------

def test_read_brief_and_read_parked_posts_agree_on_the_parked_set(session, tmp_path):
    """`read_brief`'s post section was a pure recency window (LIMIT 14 by week_start), so a
    post parked weeks ago vanished from it while `read_parked_posts` still returned it.

    read_brief is the read the planner makes FIRST, and a backlog it cannot see is a backlog
    it plans over. Found in production by the agent, recorded in agent memory, never in the
    gap register — which is its own finding.
    """
    import importlib.util
    import json as json_lib
    from pathlib import Path

    # a full recent week that would fill the 14-row window on its own
    for i in range(14):
        session.add(Post(post_id=f"post_{7000 + i}", week_start=date(2026, 8, 24),
                         channel="instagram", status="PUBLISHED", angle=f"recent{i}"))
    # and posts parked long ago — post_1485 is exactly this case in production
    old_parked = [f"post_{1485 + i}" for i in range(3)]
    for pid in old_parked:
        session.add(Post(post_id=pid, week_start=date(2026, 6, 1), channel="tiktok",
                         status="PARKED", angle="old parked", park_reason="needs an asset"))
    session.commit()

    spec = importlib.util.spec_from_file_location(
        "brief_tools", Path(__file__).resolve().parents[1].parent / "plugins" / "artec" / "tools.py")
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)

    engine = session.get_bind()
    brief = json_lib.loads(tools.HANDLERS["read_brief"]({}, engine=engine))["data"]
    parked_tool = json_lib.loads(
        tools.HANDLERS["read_parked_posts"]({}, engine=engine))["data"]

    from_tool = {p["post_id"] for p in parked_tool}
    assert from_tool == set(old_parked), "fixture sanity"
    missing = [pid for pid in from_tool if pid not in brief]
    assert not missing, f"read_brief omits PARKED posts read_parked_posts returns: {missing}"
    assert "parked_awaiting_assets: 3" in brief, "the count line must stay authoritative"
