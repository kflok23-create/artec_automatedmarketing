"""v4 seam — acceptance 1-6, 6B, 24. The fifteen tools, draft enumeration without window
loss, first-class injection, and metric transcription that cannot originate a number."""

import importlib.util
import json
from datetime import date

import pytest

from app.models import Metric, Post

WEEK = date(2026, 8, 17)


@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_v4_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(tools, name, engine, **args):
    raw = tools.HANDLERS[name](args, engine=engine)
    assert isinstance(raw, str), f"{name} must return a JSON STRING"
    body = json.loads(raw)
    assert body["ok"] is True, f"{name} errored: {body.get('error')}"
    return body["data"]


def err(tools, name, engine, **args) -> str:
    body = json.loads(tools.HANDLERS[name](args, engine=engine))
    assert body["ok"] is False, f"{name} unexpectedly succeeded: {body}"
    return body["error"]


# ---- acceptance 3: draft enumeration survives a cadence of 30 ---------------------------

def test_read_draft_posts_returns_every_draft_no_window_loss(session, tools, engine):
    # v_brief's post section is LIMIT 14; the gate previously fished drafts out of it.
    for i in range(30):
        session.add(Post(post_id=f"post_{5000 + i}", week_start=WEEK, channel="instagram",
                         status="DRAFT", angle=f"a{i}", hook=f"hook {i}",
                         cta_type="discount", cta_placement="caption_end",
                         keywords=["k"], slot="evening"))
    session.add(Post(post_id="post_5999", week_start=WEEK, channel="tiktok",
                     status="APPROVED", hook="already approved", slot="lunch"))
    session.commit()

    drafts = call(tools, "read_draft_posts", engine, week_start=str(WEEK))
    assert len(drafts) == 30, "every draft must be enumerated — no 14-row window"
    assert all(d["status"] == "DRAFT" for d in drafts)
    # full creative genome, not a summary line
    first = drafts[0]
    for field in ("post_id", "channel", "angle", "hook", "cta_type", "cta_placement",
                  "keywords", "slot"):
        assert field in first


# ---- acceptance 4: inject is a first-class path -----------------------------------------

def test_inject_with_null_post_id_creates_an_approved_post(session, tools, engine):
    before = session.query(Post).count()
    out = call(tools, "record_gate_decision", engine, post_id=None, action="inject",
               edits={"channel": "tiktok", "hook": "operator's own idea", "slot": "evening"})
    assert out["created"] is True and out["status"] == "APPROVED"
    session.expire_all()
    created = session.get(Post, out["post_id"])
    assert created.status == "APPROVED"
    assert created.hook == "operator's own idea"
    assert created.plan_source == "agent"
    assert created.gate_action["action"] == "inject"
    assert created.tracked_url and f"utm_campaign={created.post_id}" in created.tracked_url
    assert session.query(Post).count() == before + 1


def test_inject_rejects_an_off_vocabulary_slot(session, tools, engine):
    # A7: a slot that is not a key of slot_times would never publish and never error.
    message = err(tools, "record_gate_decision", engine, post_id=None, action="inject",
                  edits={"channel": "tiktok", "hook": "x", "slot": "afternoon"})
    assert "afternoon" in message and "slot_times" in message


# ---- acceptance 5, 6, 24: transcription, not origination --------------------------------

def _published(session, pid="post_6001", channel="tiktok"):
    session.add(Post(post_id=pid, week_start=WEEK, channel=channel, status="PUBLISHED",
                     hook="h", slot="evening"))
    session.commit()
    return pid


# The four hook cases that lived here (digit absent, digits present, thousands separators,
# agent arithmetic) tested the guard against the `operator_message` the AGENT passed in —
# the agent supplied both sides, so they proved nothing about origination. They are
# superseded verbatim, against a real session transcript, by
# tests/unit/test_v4_transcription_guard.py. Removed rather than duplicated: a suite that
# counts the same case twice reports coverage it does not have.


def test_hook_requires_the_operator_message(session, tools, engine):
    verdict = tools.pre_tool_call("record_metrics", args={
        "post_id": "post_6001", "channel": "tiktok", "metric_date": str(WEEK),
        "figures": {"impressions": 10}, "operator_message": "  ",
    })
    assert verdict and verdict["action"] == "block"


def test_record_metrics_stores_verbatim_and_leaves_omitted_fields_null(session, tools, engine):
    _published(session, "post_6002")
    message = "post_6002 did 4200 impressions and 118 clicks — didn't check saves"
    call(tools, "record_metrics", engine, post_id="post_6002", channel="tiktok",
         metric_date=str(WEEK), figures={"impressions": 4200, "clicks": 118},
         operator_message=message, confirm=True)
    session.expire_all()
    row = session.get(Metric, ("post_6002", "tiktok", WEEK))
    assert row.impressions == 4200 and row.clicks == 118
    assert row.saves is None and row.shares is None   # omitted stays NULL, never 0
    assert row.source == "operator_via_agent"
    assert row.operator_message == message            # verbatim audit trail


def test_record_metrics_upsert_never_zeroes_an_existing_field(session, tools, engine):
    _published(session, "post_6003")
    call(tools, "record_metrics", engine, post_id="post_6003", channel="tiktok",
         metric_date=str(WEEK), figures={"impressions": 500},
         operator_message="500 impressions", confirm=True)
    call(tools, "record_metrics", engine, post_id="post_6003", channel="tiktok",
         metric_date=str(WEEK), figures={"clicks": 12}, operator_message="12 clicks",
         confirm=True)
    session.expire_all()
    row = session.get(Metric, ("post_6003", "tiktok", WEEK))
    assert row.impressions == 500 and row.clicks == 12


# ---- acceptance 6B: no approval without delivery -----------------------------------------

def test_handlers_never_raise_and_refusals_are_ok_false_envelopes(session, tools, engine):
    """§0.1 — the documented contract is (args, **kwargs) -> str returning a JSON envelope
    ALWAYS, never raising. A raising handler would break the tool loop and could take down
    the gate session. The missing-receipt refusal raises INTERNALLY; the envelope converts
    it to ok:false. Assert the boundary behaviour, not the internal mechanism."""
    session.add(Post(post_id="post_6099", week_start=WEEK, channel="tiktok",
                     status="RENDERED", hook="h", slot="evening"))
    session.commit()

    raw = tools.HANDLERS["review_video"](
        {"post_id": "post_6099", "decision": "approve"}, engine=engine)
    assert isinstance(raw, str), "handler must return a string, never raise"
    body = json.loads(raw)
    assert body["ok"] is False                      # unambiguous failure to the agent
    assert "deliver_video" in body["error"]

    # Every handler, hit with junk, still returns a parseable envelope rather than raising.
    for name, handler in tools.HANDLERS.items():
        out = handler({}, engine=engine)
        assert isinstance(out, str), f"{name} raised instead of returning an envelope"
        assert "ok" in json.loads(out), f"{name} returned a non-envelope payload"


def test_post_ids_come_from_the_sequence_not_config(session, tools, engine):
    """§0 — the injection path must not touch config. Two injections take consecutive ids
    from the allocator and leave no post_id_counter row behind."""
    from app.models import Config

    a = call(tools, "record_gate_decision", engine, post_id=None, action="inject",
             edits={"channel": "instagram", "hook": "first", "slot": "evening"})
    b = call(tools, "record_gate_decision", engine, post_id=None, action="inject",
             edits={"channel": "instagram", "hook": "second", "slot": "evening"})
    assert int(b["post_id"].split("_")[1]) == int(a["post_id"].split("_")[1]) + 1
    session.expire_all()
    assert session.get(Config, "post_id_counter") is None


def test_review_video_refused_without_a_delivery_receipt(session, tools, engine):
    session.add(Post(post_id="post_6100", week_start=WEEK, channel="tiktok",
                     status="RENDERED", hook="h", slot="evening",
                     media_drive_file_id="gen_v1"))
    session.commit()
    message = err(tools, "review_video", engine, post_id="post_6100", decision="approve")
    assert "no Telegram delivery recorded" in message
    session.expire_all()
    assert session.get(Post, "post_6100").status == "RENDERED"   # unchanged


def test_review_video_approve_after_delivery_sets_approved_to_send(session, tools, engine):
    session.add(Post(post_id="post_6101", week_start=WEEK, channel="tiktok",
                     status="RENDERED", hook="h", slot="evening",
                     media_drive_file_id="gen_v2",
                     video_review={"telegram_message_id": 4242,
                                   "delivered_at": "2026-08-17T21:00:00+00:00"}))
    session.commit()
    out = call(tools, "review_video", engine, post_id="post_6101", decision="approve")
    assert out["status"] == "APPROVED_TO_SEND"
    session.expire_all()
    post = session.get(Post, "post_6101")
    assert post.status == "APPROVED_TO_SEND"
    assert post.video_review["decision"] == "approve"


def test_review_video_rerender_returns_to_approved_with_guidance(session, tools, engine):
    session.add(Post(post_id="post_6102", week_start=WEEK, channel="tiktok",
                     status="RENDERED", hook="h", slot="evening",
                     video_review={"telegram_message_id": 99}))
    session.commit()
    call(tools, "review_video", engine, post_id="post_6102", decision="rerender",
         reason="crop cuts the build off at the top")
    session.expire_all()
    post = session.get(Post, "post_6102")
    assert post.status == "APPROVED"
    assert post.video_review["rerender_guidance"] == "crop cuts the build off at the top"


def test_review_video_reject_parks_with_reason(session, tools, engine):
    session.add(Post(post_id="post_6103", week_start=WEEK, channel="tiktok",
                     status="RENDERED", hook="h", slot="evening",
                     video_review={"telegram_message_id": 7}))
    session.commit()
    call(tools, "review_video", engine, post_id="post_6103", decision="reject",
         reason="wrong product generation")
    session.expire_all()
    post = session.get(Post, "post_6103")
    assert post.status == "PARKED" and "wrong product" in post.park_reason


# ---- email review ------------------------------------------------------------------------

EMAIL_COPY = json.dumps({"subject": "s", "headline": "h", "body_copy": "b",
                         "cta_text": "c", "story_block": "st"})


def _email_post(session, pid="post_6200"):
    session.add(Post(post_id=pid, week_start=WEEK, channel="email", status="RENDERED",
                     hook="h", slot="morning", caption=EMAIL_COPY,
                     media_drive_file_id="gen_e1"))
    session.commit()
    return pid


def test_review_email_approve_sets_approved_to_send_not_sent(session, tools, engine):
    _email_post(session)
    out = call(tools, "review_email", engine, post_id="post_6200", decision="approve")
    assert out["status"] == "APPROVED_TO_SEND"
    session.expire_all()
    post = session.get(Post, "post_6200")
    assert post.status == "APPROVED_TO_SEND"
    assert post.external_post_id is None      # nothing sent at review time


def test_review_email_edit_overwrites_variables_and_re_presents(session, tools, engine):
    _email_post(session, "post_6201")
    out = call(tools, "review_email", engine, post_id="post_6201", decision="edit",
               edits={"subject": "a sharper subject", "cta_text": "Start building"})
    assert out["status"] == "RENDERED" and out["re_presented"] == "next digest"
    session.expire_all()
    post = session.get(Post, "post_6201")
    copy = json.loads(post.caption)
    assert copy["subject"] == "a sharper subject"
    assert copy["cta_text"] == "Start building"
    assert copy["body_copy"] == "b"           # untouched variables survive


def test_review_email_rejects_non_contract_variables(session, tools, engine):
    _email_post(session, "post_6202")
    body = json.loads(tools.HANDLERS["review_email"](
        {"post_id": "post_6202", "decision": "edit", "edits": {"list_id": 99}},
        engine=engine))
    assert body["ok"] is True and "not editable" in body["data"]["error"]


def test_review_email_reject_parks_and_never_sends(session, tools, engine):
    _email_post(session, "post_6203")
    call(tools, "review_email", engine, post_id="post_6203", decision="reject")
    session.expire_all()
    post = session.get(Post, "post_6203")
    assert post.status == "PARKED" and post.external_post_id is None


def test_review_email_is_idempotent(session, tools, engine):
    _email_post(session, "post_6204")
    call(tools, "review_email", engine, post_id="post_6204", decision="approve")
    out = call(tools, "review_email", engine, post_id="post_6204", decision="reject")
    assert out["already"] == "approve"


# ---- recovery actions --------------------------------------------------------------------

def test_retry_post_refuses_when_already_published(session, tools, engine):
    session.add(Post(post_id="post_6300", week_start=WEEK, channel="instagram",
                     status="FAILED", hook="h", slot="evening",
                     external_post_id="up_123"))
    session.commit()
    out = call(tools, "retry_post", engine, post_id="post_6300")
    assert "double-publish" in out["error"]


def test_retry_post_returns_rendered_post_to_publish(session, tools, engine):
    session.add(Post(post_id="post_6301", week_start=WEEK, channel="instagram",
                     status="FAILED", hook="h", slot="evening",
                     media_drive_file_id="gen_1", park_reason="upload timeout"))
    session.commit()
    out = call(tools, "retry_post", engine, post_id="post_6301")
    assert out["status"] == "RENDERED" and out["previous_failure"] == "upload timeout"


def test_fulfil_wishlist_returns_parked_post_to_approved(session, tools, engine):
    from app.models import Asset

    session.add(Post(post_id="post_6400", week_start=WEEK, channel="tiktok",
                     status="PARKED", hook="h", slot="evening",
                     asset_wishlist=[{"target_folder": "raw-video/assembled",
                                      "medium": "video", "description": "d"}]))
    session.add(Asset(drive_file_id="newclip", drive_path="raw-video/assembled/c.mp4",
                      medium="video", subject="assembled_blocks", status="active"))
    session.commit()
    out = call(tools, "fulfil_wishlist", engine, post_id="post_6400", drive_file_id="newclip")
    assert out["status"] == "APPROVED"
    session.expire_all()
    post = session.get(Post, "post_6400")
    assert post.status == "APPROVED"
    assert post.asset_wishlist[0]["fulfilled_by"] == "newclip"
