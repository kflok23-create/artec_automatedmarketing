"""Acceptance 14 (render → _generated + persisted id), 15 (double-publish guard)."""

from datetime import date

import pytest

from app.config import set_config
from app.integrations.fakes import FakeBrevo, FakeDrive, FakeFal, FakeLLM, FakeUploadPost
from app.models import Asset, Post
from app.stages.publish import DoublePublishError, publish
from app.stages.render import render

WEEK = date(2026, 8, 3)


def _approved(session, pid="post_1495", channel="instagram"):
    p = Post(post_id=pid, week_start=WEEK, channel=channel, status="APPROVED",
             angle="focus", hook="Build focus, one block at a time",
             cta_type="discount", cta_placement="caption_end",
             tracked_url=f"https://artec.my/?code=SOCIAL50&utm_source={channel}&utm_medium=organic&utm_campaign={pid}")
    session.add(p)
    session.flush()
    return p


def _bank_asset(session):
    session.add(Asset(drive_file_id="asset_1", drive_path="raw-photo/assembled/a.jpg",
                      medium="photo", subject="assembled_blocks", has_person=False,
                      aspect="square", status="active"))
    session.flush()


def test_render_uploads_to_generated_and_persists_ids(session):
    _bank_asset(session)
    post = _approved(session)
    drive = FakeDrive()
    out = render(session, FakeLLM(), drive, FakeFal(), all_approved=True, log=lambda *_: None)
    assert out["rendered"] == 1
    assert post.status == "RENDERED"
    assert post.media_drive_file_id is not None        # 14
    assert drive.uploaded and drive.uploaded[0][1] == str(WEEK)
    assert drive.uploaded[0][2].startswith("post_1495")
    assert post.source_asset_ids == ["asset_1"]
    assert session.get(Asset, "asset_1").times_used == 1


def test_render_parks_when_bank_empty_for_video(session):
    post = _approved(session, pid="post_1496", channel="tiktok")  # video channel, empty bank
    out = render(session, FakeLLM(), FakeDrive(), FakeFal(), all_approved=True, log=lambda *_: None)
    assert out["parked"] == 1
    assert post.status == "PARKED"
    assert post.asset_wishlist, "a parked post carries a structured wishlist"


def test_double_publish_refused_and_writes_nothing(session):
    post = _approved(session, pid="post_1497")
    post.status = "RENDERED"
    post.media_drive_file_id = "gen_1"
    post.caption = "hello"
    post.external_post_id = "already-published-123"     # simulate a prior publish
    session.flush()
    set_config(session, "confirm_first_publish", False)
    uploader = FakeUploadPost()
    with pytest.raises(DoublePublishError):
        publish(session, FakeDrive(), FakeFal(), uploader, FakeBrevo(),
                all_rendered=True, confirm=False, log=lambda *_: None)
    assert uploader.calls == []                          # 15: nothing was sent
    assert post.posted_at is None
    assert post.status == "RENDERED"


def test_publish_photo_flow_sets_external_id(session):
    post = _approved(session, pid="post_1498")
    post.status = "RENDERED"
    post.media_drive_file_id = "gen_1"
    post.caption = "hello"
    session.flush()
    set_config(session, "confirm_first_publish", False)
    uploader = FakeUploadPost()
    out = publish(session, FakeDrive(), FakeFal(), uploader, FakeBrevo(),
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 1
    assert post.status == "PUBLISHED" and post.external_post_id
    assert uploader.calls[0]["platform"] == "instagram"
    assert post.tracked_url in uploader.calls[0]["title"]  # the spine rides the caption


def test_failed_publish_records_reason_and_never_lost_the_media(session):
    # A publish failure keeps the render (media ids intact) and records why, so
    # `hermes post retry` can flip it back to RENDERED for another attempt.
    post = _approved(session, pid="post_1501")
    post.status = "RENDERED"
    post.media_drive_file_id = "gen_1"
    post.caption = "hello"
    session.flush()
    set_config(session, "confirm_first_publish", False)

    class _BoomUploader(FakeUploadPost):
        def upload_photo(self, platform, photo_path, title):
            raise TypeError("sequence item 1: expected a bytes-like object, tuple found")

    out = publish(session, FakeDrive(), FakeFal(), _BoomUploader(), FakeBrevo(),
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 0
    assert post.status == "FAILED"
    assert "TypeError" in post.park_reason
    assert post.external_post_id is None          # nothing went live → retry is safe
    assert post.media_drive_file_id == "gen_1"    # render preserved

    # The retry transition itself (what `hermes post retry` performs):
    post.status = "RENDERED"
    post.park_reason = None
    session.flush()
    out = publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 1
    assert post.status == "PUBLISHED" and post.external_post_id


def test_email_publish_uses_template_contract(session):
    post = _approved(session, pid="post_1499", channel="email")
    post.status = "RENDERED"
    post.media_drive_file_id = "gen_1"
    post.caption = ('{"subject": "s", "headline": "h", "body_copy": "b", '
                    '"cta_text": "c", "story_block": "st"}')
    session.flush()
    set_config(session, "confirm_first_publish", False)
    brevo = FakeBrevo()
    out = publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), brevo,
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 1
    assert brevo.sent == [1]
    html = brevo.campaigns[0]["html"]
    assert "{{ mirror }}" in html and "{{ unsubscribe }}" in html
    assert "{{headline}}" not in html
    assert brevo.campaigns[0]["subject"] == "s"
    assert post.external_post_id == "1"
