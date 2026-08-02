"""Acceptance 11 (reject = no regen), 12 (structured park), 13 (wishlist match)."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.integrations.fakes import FakeTelegram
from app.models import Asset, Post
from app.stages.gate import gate
from app.stages.wishlist import match
from app.toolbox.park import ParkError, park_post, validate_wishlist

WEEK = date(2026, 8, 3)


def _draft(session, pid="post_1490", channel="instagram"):
    p = Post(post_id=pid, week_start=WEEK, channel=channel, status="DRAFT",
             angle="a", hook="h", cta_type="discount", cta_placement="caption_end")
    session.add(p)
    session.flush()
    return p


def test_rejected_draft_stays_rejected_no_replacement(session):
    _draft(session)
    before = session.execute(select(func.count()).select_from(Post)).scalar()
    tg = FakeTelegram(scripted_updates=[
        [{"update_id": 1, "callback_query": {"id": "cb1", "data": "rej:post_1490"}}],
        [{"update_id": 2, "message": {"text": "/done"}}],
    ])
    gate(session, tg, timeout=5, log=lambda *_: None)
    assert session.get(Post, "post_1490").status == "REJECTED"
    after = session.execute(select(func.count()).select_from(Post)).scalar()
    assert after == before  # 11: a rejected slot means fewer posts — never a regenerated one


def test_gate_approve_and_inject(session):
    _draft(session)
    tg = FakeTelegram(scripted_updates=[
        [{"update_id": 1, "callback_query": {"id": "cb1", "data": "app:post_1490"}}],
        [{"update_id": 2, "message": {"text": "+ channel: tiktok | hook: kids love this"}}],
        [{"update_id": 3, "message": {"text": "/done"}}],
    ])
    counts = gate(session, tg, timeout=5, log=lambda *_: None)
    assert counts["approved"] == 1 and counts["injected"] == 1
    injected = [p for p in session.execute(select(Post)).scalars() if p.post_id != "post_1490"]
    assert injected[0].status == "APPROVED" and injected[0].channel == "tiktok"
    assert injected[0].tracked_url and "utm_campaign=" + injected[0].post_id in injected[0].tracked_url


def test_park_requires_valid_taxonomy_folder(session):
    p = _draft(session, "post_1491")
    with pytest.raises(ParkError):
        park_post(session, p, "no asset", [{"target_folder": "raw-photo/child_face",
                                            "medium": "photo", "description": "x"}])
    park_post(session, p, "no asset", [{"target_folder": "raw-video/child-face",
                                        "medium": "video", "aspect": "vertical",
                                        "duration_s": "8-15", "description": "child snapping blocks"}])
    assert p.status == "PARKED"
    assert p.asset_wishlist[0]["target_folder"] == "raw-video/child-face"  # 12


def test_empty_wishlist_is_invalid():
    with pytest.raises(ParkError):
        validate_wishlist([])


def test_wishlist_match_returns_parked_to_approved(session):
    p = _draft(session, "post_1492")
    park_post(session, p, "needs asset", [{"target_folder": "raw-photo/assembled",
                                           "medium": "photo", "aspect": "square",
                                           "description": "assembled crane"}])
    out = match(session, log=lambda *_: None)
    assert out["returned"] == 0  # nothing in the bank yet

    session.add(Asset(drive_file_id="new1", drive_path="raw-photo/assembled/crane.jpg",
                      medium="photo", subject="assembled_blocks", has_person=False,
                      aspect="square", status="active"))
    session.flush()
    out = match(session, log=lambda *_: None)   # 13: after sync, parked returns to APPROVED
    assert out["returned"] == 1
    assert p.status == "APPROVED"
    assert p.asset_wishlist[0]["fulfilled_by"] == "new1"
