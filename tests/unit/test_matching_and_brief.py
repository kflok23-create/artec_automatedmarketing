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


def test_v_brief_capped_at_40_rows(session):
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
    assert 0 < len(rows) <= 40


def test_selector_bank_first_violation(session):
    candidates = [_asset(1)]
    with pytest.raises(PlanError, match="bank-first"):
        validate_plan({"subject": "assembled_blocks", "tools": ["generate"], "asset_ids": [],
                       "prompt": "x"}, candidates, allow_person=False)


def test_selector_rejects_foreign_asset_ids_and_gated_person():
    candidates = [_asset(1), _asset(2, has_person=True)]
    with pytest.raises(PlanError, match="not in the offered"):
        validate_plan({"subject": "assembled_blocks", "tools": ["asset"], "asset_ids": ["zz"]},
                      candidates, allow_person=False)
    with pytest.raises(PlanError, match="gated off"):
        validate_plan({"subject": "assembled_blocks", "tools": ["asset"], "asset_ids": ["a2"]},
                      candidates, allow_person=False)


def test_fallback_plan_prefers_bank_then_generate_then_text_card():
    with_bank = fallback_plan("assembled_blocks", [_asset(1)], "photo")
    assert with_bank.tools[0] == "asset" and with_bank.asset_ids == ["a1"]
    empty_generatable = fallback_plan("assembled_blocks", [], "photo")
    assert empty_generatable.tools == ["generate"]
    empty_other = fallback_plan("classroom", [], "photo")
    assert empty_other.tools == ["text_card"]
    assert fallback_plan("child_face", [], "video") is None  # video + empty bank → park
