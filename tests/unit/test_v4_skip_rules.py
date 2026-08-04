"""v4 §B (the two skip rules) and §E (the gates end to end).

Acceptance 6A–6G — video · 6a–6h — email. Each test names the clause it holds.

These are RULES, not ordering accidents: `skip_reason()` is evaluated for every post on
every publish pass, before anything else happens, and it is a pure function so it can be
asserted directly rather than inferred from what happened to publish.
"""

import importlib.util
import json
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta

import pytest
import respx

from app.config import set_config
from app.integrations.fakes import FakeBrevo, FakeDrive, FakeFal, FakeUploadPost
from app.models import Post
from app.scheduler import select_due_posts, sweep_expired_reviews
from app.stages.publish import carries_video, publish, skip_reason

WEEK = date(2026, 8, 24)
NOW = datetime(2026, 8, 27, 21, 0, tzinfo=UTC)
API = "https://api.telegram.org"


@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_gate_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(tools, name, engine, **args):
    body = json.loads(tools.HANDLERS[name](args, engine=engine))
    assert body["ok"] is True, f"{name} errored: {body.get('error')}"
    return body["data"]


def err(tools, name, engine, **args) -> str:
    body = json.loads(tools.HANDLERS[name](args, engine=engine))
    assert body["ok"] is False, f"{name} unexpectedly succeeded: {body}"
    return body["error"]


EMAIL_COPY = json.dumps({"subject": "s", "headline": "h", "body_copy": "b",
                         "cta_text": "c", "story_block": "st"})


def _email(session, pid="post_2001", status="RENDERED", review=None):
    session.add(Post(post_id=pid, week_start=WEEK, channel="email", status=status,
                     slot="morning", media_drive_file_id="gen_1", caption=EMAIL_COPY,
                     tracked_url="https://artec.my/?code=EMAIL50", email_review=review))
    session.commit()
    return pid


def _video(session, pid="post_2101", status="RENDERED", review=None, channel="tiktok"):
    session.add(Post(post_id=pid, week_start=WEEK, channel=channel, status=status,
                     slot="evening", media_drive_file_id="gen_1.mp4", caption="hook",
                     tracked_url="https://artec.my/?code=SOCIAL50", video_review=review))
    session.commit()
    return pid


def _run_publish(session, **kw):
    set_config(session, "confirm_first_publish", False)
    return publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                   all_rendered=True, confirm=False, log=lambda *_: None, **kw)


# =========================================================================================
# §B · SKIP RULE 1 — email never auto-publishes
# =========================================================================================

def test_6a_a_rendered_email_is_skipped_regardless_of_slot(session):
    _email(session)
    reason = skip_reason(session, session.get(Post, "post_2001"))
    assert reason and "email never auto-publishes" in reason
    assert _run_publish(session) == {"published": 0, "skipped": 1}


def test_6b_a_direct_publish_of_a_rendered_email_refuses(session):
    _email(session, "post_2002")
    out = publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                  post_ids=["post_2002"], confirm=False, log=lambda *_: None)
    assert out["published"] == 0 and out["skipped"] == 1
    assert session.get(Post, "post_2002").status == "RENDERED"


def test_6c_approved_to_send_without_a_recorded_approval_still_refuses(session):
    """Status is reachable by routes the review never took. The receipt is not."""
    _email(session, "post_2003", status="APPROVED_TO_SEND", review=None)
    reason = skip_reason(session, session.get(Post, "post_2003"))
    assert reason and "recorded approval" in reason
    assert _run_publish(session)["published"] == 0


def test_6d_an_approved_email_publishes(session):
    _email(session, "post_2004", status="APPROVED_TO_SEND",
           review={"decision": "approve"})
    assert _run_publish(session)["published"] == 1
    assert session.get(Post, "post_2004").status == "PUBLISHED"


# =========================================================================================
# §B · SKIP RULE 2 — video never auto-publishes
# =========================================================================================

def test_6A_video_is_detected_by_channel_media_and_by_extension(session):
    _video(session, "post_2101")
    assert carries_video(session, session.get(Post, "post_2101")) is True
    session.add(Post(post_id="post_2102", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening", media_drive_file_id="gen_2.mp4"))
    session.flush()
    assert carries_video(session, session.get(Post, "post_2102")) is True, \
        "an .mp4 on a photo channel is still video — the file decides too"


def test_6B_a_rendered_video_is_skipped(session):
    _video(session, "post_2103")
    reason = skip_reason(session, session.get(Post, "post_2103"))
    assert reason and "video never auto-publishes" in reason
    assert _run_publish(session) == {"published": 0, "skipped": 1}


def test_6C_approved_without_a_delivery_receipt_refuses(session):
    _video(session, "post_2104", status="APPROVED_TO_SEND",
           review={"decision": "approve"})           # approved, but never delivered
    reason = skip_reason(session, session.get(Post, "post_2104"))
    assert reason and "never shown" in reason


def test_6D_an_approved_and_delivered_video_passes_the_gate(session):
    _video(session, "post_2105", status="APPROVED_TO_SEND",
           review={"decision": "approve", "telegram_message_id": 8801})
    assert skip_reason(session, session.get(Post, "post_2105")) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not on PATH here — publishing a video runs the "
                           "real pre-flight, and a mocked probe would prove nothing")
def test_6D_and_publishes_when_the_file_is_real(session, tmp_path):
    out_path = str(tmp_path / "real.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=12:r=30",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-movflags", "+faststart", "-y", out_path],
        check=True, capture_output=True, timeout=300)

    class RealVideoDrive(FakeDrive):
        def download(self, file_id, suffix=""):
            return out_path

    _video(session, "post_2105b", status="APPROVED_TO_SEND",
           review={"decision": "approve", "telegram_message_id": 8801})
    set_config(session, "confirm_first_publish", False)
    out = publish(session, RealVideoDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 1
    assert session.get(Post, "post_2105b").status == "PUBLISHED"


# =========================================================================================
# §B · slot timing and pre-flight
# =========================================================================================

def test_6E_approved_to_send_waits_for_the_next_occurrence_of_its_slot(session):
    _video(session, "post_2106", status="APPROVED_TO_SEND",
           review={"decision": "approve", "telegram_message_id": 1})
    assert [p.post_id for p in select_due_posts(session, "evening")] == ["post_2106"]
    assert select_due_posts(session, "morning") == [], "not at 21:15, and not at any other slot"


def test_6F_publish_preflight_is_wired_and_parks_a_broken_file(session, tmp_path, monkeypatch):
    """The first time A is load-bearing. A truncated image must park, not ship."""
    broken = tmp_path / "truncated.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 2000)     # header, no image data

    class BrokenDrive(FakeDrive):
        def download(self, file_id, suffix=""):
            return str(broken)

    session.add(Post(post_id="post_2107", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening", media_drive_file_id="gen_9",
                     caption="hook", tracked_url="https://artec.my/"))
    session.commit()
    set_config(session, "confirm_first_publish", False)
    out = publish(session, BrokenDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                  all_rendered=True, confirm=False, log=lambda *_: None)
    assert out["published"] == 0
    post = session.get(Post, "post_2107")
    assert post.status == "PARKED"
    assert "pre-flight" in post.park_reason
    assert post.asset_wishlist and post.asset_wishlist[0]["target_folder"].startswith("raw-")


def test_6G_a_post_with_no_drive_file_refuses_rather_than_using_a_fallback(session):
    session.add(Post(post_id="post_2108", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="evening", media_drive_file_id=None,
                     media_url="https://v3.fal.media/x.jpg", caption="hook"))
    session.commit()
    set_config(session, "confirm_first_publish", False)
    publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
            all_rendered=True, confirm=False, log=lambda *_: None)
    post = session.get(Post, "post_2108")
    assert post.status == "FAILED" and "MediaNotInDrive" in post.park_reason


# =========================================================================================
# §E · review_email — every branch
# =========================================================================================

def test_6e_approve_sets_approved_to_send_never_sends_now(session, tools, engine):
    _email(session, "post_2201")
    out = call(tools, "review_email", engine, post_id="post_2201", decision="approve")
    assert out["status"] == "APPROVED_TO_SEND"
    session.expire_all()
    assert session.get(Post, "post_2201").external_post_id is None


def test_6f_reject_parks_with_the_reason(session, tools, engine):
    _email(session, "post_2202")
    call(tools, "review_email", engine, post_id="post_2202", decision="reject",
         edits={"reason": "the discount is wrong"})
    session.expire_all()
    post = session.get(Post, "post_2202")
    assert post.status == "PARKED" and "discount is wrong" in post.park_reason


def test_6g_edit_overwrites_any_of_the_seven_variables_and_re_presents(session, tools, engine):
    _email(session, "post_2203")
    out = call(tools, "review_email", engine, post_id="post_2203", decision="edit",
               edits={"subject": "new subject", "cta_text": "Get RM40 off"})
    assert out["status"] == "RENDERED" and out["re_presented"] == "next digest"
    session.expire_all()
    copy = json.loads(session.get(Post, "post_2203").caption)
    assert copy["subject"] == "new subject" and copy["cta_text"] == "Get RM40 off"
    assert copy["headline"] == "h", "untouched variables survive an edit"


def test_6h_an_off_contract_variable_is_refused(session, tools, engine):
    _email(session, "post_2204")
    out = call(tools, "review_email", engine, post_id="post_2204", decision="edit",
               edits={"send_at": "now"})
    assert "not editable" in out["error"]


def test_6i_send_test_changes_no_status(session, tools, engine):
    _email(session, "post_2205")
    out = call(tools, "review_email", engine, post_id="post_2205", decision="test_only",
               send_test=True)
    assert out["status_unchanged"] == "RENDERED"
    session.expire_all()
    assert session.get(Post, "post_2205").status == "RENDERED"
    assert session.get(Post, "post_2205").email_review["test_sends"]


# =========================================================================================
# §E · review_video — every branch
# =========================================================================================

DELIVERED = {"decision": None, "telegram_message_id": 8801}


def test_review_video_approve_reject_rerender(session, tools, engine):
    _video(session, "post_2301", review=dict(DELIVERED))
    assert call(tools, "review_video", engine, post_id="post_2301",
                decision="approve")["status"] == "APPROVED_TO_SEND"

    _video(session, "post_2302", review=dict(DELIVERED))
    call(tools, "review_video", engine, post_id="post_2302", decision="reject",
         reason="the hands are wrong")
    session.expire_all()
    assert session.get(Post, "post_2302").status == "PARKED"
    assert "hands are wrong" in session.get(Post, "post_2302").park_reason

    _video(session, "post_2303", review=dict(DELIVERED))
    out = call(tools, "review_video", engine, post_id="post_2303", decision="rerender",
               reason="cut the first second")
    assert out["status"] == "APPROVED", "rerender re-enters the render queue"
    session.expire_all()
    review = session.get(Post, "post_2303").video_review
    assert review["rerender_guidance"] == "cut the first second", \
        "the operator's reason reaches the toolbox as guidance"


# =========================================================================================
# §E · expiry — no auto-approve exists to be requested
# =========================================================================================

def _aged(iso_days_ago: int) -> str:
    return (NOW - timedelta(days=iso_days_ago)).isoformat()


def test_an_unanswered_email_review_parks_at_three_days(session):
    _email(session, "post_2401", review={"presented_at": _aged(3)})
    expired = sweep_expired_reviews(session, now=NOW, log=lambda *_: None)
    assert [e["post_id"] for e in expired] == ["post_2401"]
    post = session.get(Post, "post_2401")
    assert post.status == "PARKED" and "expired" in post.park_reason


def test_an_unanswered_video_review_parks_at_three_days(session):
    _video(session, "post_2402", review={"delivered_at": _aged(4),
                                         "telegram_message_id": 1})
    expired = sweep_expired_reviews(session, now=NOW, log=lambda *_: None)
    assert expired[0]["surface"] == "video"
    assert session.get(Post, "post_2402").status == "PARKED"


def test_expiry_never_sends_and_never_approves(session):
    _email(session, "post_2403", review={"presented_at": _aged(30)})
    sweep_expired_reviews(session, now=NOW, log=lambda *_: None)
    post = session.get(Post, "post_2403")
    assert post.status == "PARKED", "not APPROVED_TO_SEND, not PUBLISHED, at any age"
    assert post.external_post_id is None
    assert (post.email_review or {}).get("decision") is None


def test_a_review_inside_its_window_is_left_alone(session):
    _email(session, "post_2404", review={"presented_at": _aged(2)})
    assert sweep_expired_reviews(session, now=NOW, log=lambda *_: None) == []
    assert session.get(Post, "post_2404").status == "RENDERED"


def test_an_answered_review_does_not_expire(session):
    _email(session, "post_2405", status="APPROVED_TO_SEND",
           review={"presented_at": _aged(9), "decision": "approve"})
    assert sweep_expired_reviews(session, now=NOW, log=lambda *_: None) == []
    assert session.get(Post, "post_2405").status == "APPROVED_TO_SEND"


def test_a_never_presented_review_has_not_expired(session):
    """The window measures the time the operator had to answer, not shelf life."""
    _email(session, "post_2406", review=None)
    assert sweep_expired_reviews(session, now=NOW, log=lambda *_: None) == []


@respx.mock
def test_an_expired_video_cannot_then_be_approved(session, tools, engine, monkeypatch):
    monkeypatch.setenv("ARTEC_API_BASE", "https://artec-api.test")
    monkeypatch.setenv("HERMES_API_TOKEN", "t")
    _video(session, "post_2407", review={"delivered_at": _aged(5),
                                         "telegram_message_id": 4})
    sweep_expired_reviews(session, now=NOW, log=lambda *_: None)
    session.commit()
    # the tool still records the decision, but the post is PARKED and publish skips it
    call(tools, "review_video", engine, post_id="post_2407", decision="approve")
    session.expire_all()
    post = session.get(Post, "post_2407")
    assert _run_publish(session)["published"] == 0
    assert post.status != "PUBLISHED"
