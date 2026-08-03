"""v4 §6 · D — digest delivery (job 12 body).

The brain is the sole Telegram owner: it reads the prepared payload, sends the pre-split
messages verbatim, delivers video NATIVELY (never a Drive link), and relays replies through
the action tools. Three things are enforced HERE rather than in the prompt, because a
prompt is a request and a refusal is a property:

  * job 12 does not run on Sunday — read_digest itself declines,
  * a video the operator was never shown cannot be approved — review_video needs a receipt,
  * nothing is written from a figure the operator has not seen echoed back.
"""

import importlib.util
import json
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.models import Digest, Metric, Post
from app.stages.digest import prepare_digest, render_digest_text

API = "https://api.telegram.org"
TARGET = date(2026, 8, 27)          # a Thursday
SUNDAY = datetime(2026, 8, 30, 21, 0, tzinfo=UTC)
MONDAY = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)


@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_delivery_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def v4(tools):
    return tools._v4


class FakeBrevoCount:
    def get_list_count(self):
        return 1


def call(tools, name, engine, **args):
    body = json.loads(tools.HANDLERS[name](args, engine=engine))
    assert body["ok"] is True, f"{name} errored: {body.get('error')}"
    return body["data"]


def err(tools, name, engine, **args) -> str:
    body = json.loads(tools.HANDLERS[name](args, engine=engine))
    assert body["ok"] is False, f"{name} unexpectedly succeeded: {body}"
    return body["error"]


def _prepared(session):
    return prepare_digest(session, brevo=FakeBrevoCount(), target=TARGET, log=lambda *_: None)


# ---- job 12 does not run on Sunday — asserted in the body, not only in the cron ----------

def test_sunday_declines_in_the_body_not_only_in_the_cron_expression(session, v4, engine):
    _prepared(session)
    session.commit()
    out = v4._read_digest_impl(str(TARGET), now=SUNDAY, engine=engine)
    assert out["deliver"] is False
    assert "Sunday" in out["skip_reason"] and "gate" in out["skip_reason"]
    assert "needs_you" not in out, "there is nothing to deliver, so nothing is handed over"


def test_sunday_refusal_does_not_mark_the_digest_delivered(session, v4, engine):
    _prepared(session)
    session.commit()
    v4._read_digest_impl(str(TARGET), now=SUNDAY, engine=engine)
    session.expire_all()
    row = session.query(Digest).filter(Digest.digest_date == TARGET).one()
    assert row.delivered_at is None


def test_any_other_evening_delivers(session, v4, engine):
    _prepared(session)
    session.commit()
    out = v4._read_digest_impl(str(TARGET), now=MONDAY, engine=engine)
    assert out["deliver"] is True and out["prepared"] is True
    assert out["messages"], "the brain sends these verbatim"


def test_an_unprepared_date_is_reported_not_invented(session, v4, engine):
    out = v4._read_digest_impl("2026-08-20", now=MONDAY, engine=engine)
    assert out["prepared"] is False and out["deliver"] is False
    assert "job 11" in out["note"]


def test_read_digest_hands_over_the_presplit_messages(session, tools, engine):
    payload = _prepared(session)
    session.commit()
    out = call(tools, "read_digest", engine, date=str(TARGET), now=MONDAY)
    assert out["messages"] == payload["messages"]
    assert "".join(out["messages"]).startswith("HERMES")
    assert render_digest_text(payload) in out["messages"][0] or len(out["messages"]) > 1


# ---- video is delivered natively, and the receipt is what unlocks the decision -----------

def _pending_video(session, pid="post_9101"):
    session.add(Post(post_id=pid, week_start=TARGET, channel="tiktok", status="RENDERED",
                     slot="evening", caption="Two blocks, four seconds, and the moment it clicks.",
                     media_drive_file_id="gen_9101.mp4",
                     video_review={"public_url": "https://v3.fal.media/files/x/gen_9101.mp4"}))
    session.commit()
    return pid


@respx.mock
def test_video_goes_out_as_a_native_telegram_video_never_a_link(session, tools, engine):
    pid = _pending_video(session)
    route = respx.post(url__regex=rf"{API}/bot.*/sendVideo").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 8801}}))

    out = call(tools, "deliver_video", engine, post_id=pid)
    assert out["telegram_message_id"] == 8801

    assert route.called, "sendVideo, not sendMessage — a link is not a viewing"
    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["video"].endswith(".mp4")
    assert "drive.google.com" not in sent["video"]
    assert sent["supports_streaming"] == "true"
    assert pid in sent["caption"]

    session.expire_all()
    assert session.get(Post, pid).video_review["telegram_message_id"] == 8801


@respx.mock
def test_review_video_is_refused_until_the_operator_has_actually_seen_it(session, tools, engine):
    pid = _pending_video(session, "post_9102")
    message = err(tools, "review_video", engine, post_id=pid, decision="approve")
    assert "deliver_video" in message

    respx.post(url__regex=rf"{API}/bot.*/sendVideo").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 8802}}))
    call(tools, "deliver_video", engine, post_id=pid)
    out = call(tools, "review_video", engine, post_id=pid, decision="approve")
    assert out["status"] == "APPROVED_TO_SEND"


@respx.mock
def test_telegram_refusing_the_upload_parks_the_post(session, tools, engine):
    """Telegram's refusal is independent evidence the file is malformed, and more
    trustworthy than our own pre-flight pass — so it parks rather than retries."""
    pid = _pending_video(session, "post_9103")
    respx.post(url__regex=rf"{API}/bot.*/sendVideo").mock(
        return_value=httpx.Response(400, json={"ok": False,
                                               "description": "Bad Request: VIDEO_CONTENT_TYPE_INVALID"}))
    out = call(tools, "deliver_video", engine, post_id=pid)
    assert out["parked"] is True
    assert "VIDEO_CONTENT_TYPE_INVALID" in out["error"]

    session.expire_all()
    post = session.get(Post, pid)
    assert post.status == "PARKED"
    assert "telegram refused" in post.park_reason
    # and it still cannot be approved: parking is not a silent shrug
    assert "deliver_video" in err(tools, "review_video", engine, post_id=pid, decision="approve")


@respx.mock
def test_a_transient_network_error_does_not_park(session, tools, engine):
    pid = _pending_video(session, "post_9104")
    respx.post(url__regex=rf"{API}/bot.*/sendVideo").mock(side_effect=httpx.ConnectTimeout("x"))
    out = call(tools, "deliver_video", engine, post_id=pid)
    assert out["parked"] is False
    session.expire_all()
    assert session.get(Post, pid).status == "RENDERED"


@respx.mock
def test_delivery_is_idempotent_within_a_session(session, tools, engine):
    pid = _pending_video(session, "post_9105")
    route = respx.post(url__regex=rf"{API}/bot.*/sendVideo").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 8805}}))
    call(tools, "deliver_video", engine, post_id=pid)
    again = call(tools, "deliver_video", engine, post_id=pid)
    assert again["already_delivered"] is True
    assert route.call_count == 1, "the operator is not shown the same video twice"


# ---- metrics entry: the multi-turn cost, designed down ----------------------------------

def test_one_ordered_line_lands_all_six_fields(v4):
    assert v4.parse_metrics_line("4200, 0.62, 12, 45, 8, 118") == {
        "impressions": 4200, "completion_rate": 0.62, "watch_time_s": 12,
        "saves": 45, "shares": 8, "clicks": 118}


def test_empty_positions_are_null_never_zero(v4):
    parsed = v4.parse_metrics_line("4200, , , 45, , 118")
    assert parsed["impressions"] == 4200 and parsed["saves"] == 45 and parsed["clicks"] == 118
    assert parsed["completion_rate"] is None
    assert parsed["watch_time_s"] is None
    assert parsed["shares"] is None
    assert 0 not in parsed.values(), "a blank is unmeasured — coercing it to 0 is a lie"


def test_a_literal_zero_is_a_measurement(v4):
    assert v4.parse_metrics_line("4200, , , 0")["saves"] == 0


def test_a_short_line_leaves_the_rest_unmeasured(v4):
    parsed = v4.parse_metrics_line("4200")
    assert parsed == {"impressions": 4200}


def test_a_thousands_separator_is_refused_rather_than_misaligned(v4):
    # '4,200, 0.62, 12, 45, 8, 118' is seven positions: every later figure would land in
    # the wrong column, which is worse than no reading at all.
    with pytest.raises(v4.MetricsLineError) as e:
        v4.parse_metrics_line("4,200, 0.62, 12, 45, 8, 118")
    assert "4200, not 4,200" in str(e.value)


def test_a_non_numeric_position_names_the_field(v4):
    with pytest.raises(v4.MetricsLineError) as e:
        v4.parse_metrics_line("4200, lots, 12")
    assert "watch_time_s" not in str(e.value) and "completion_rate" in str(e.value)


def _published(session, pid="post_9201"):
    session.add(Post(post_id=pid, week_start=TARGET, channel="tiktok", status="PUBLISHED",
                     slot="evening", hook="h"))
    session.commit()
    return pid


def test_the_first_call_writes_nothing_and_echoes_what_it_would_record(session, tools, engine):
    pid = _published(session)
    out = call(tools, "record_metrics", engine, post_id=pid, channel="tiktok",
               metric_date=str(TARGET), figures_line="4200, , , 45, , 118",
               operator_message="4200, , , 45, , 118")
    assert out["written"] is False and out["preview"] is True
    assert out["will_record"] == {"impressions": 4200, "saves": 45, "clicks": 118}
    assert out["will_stay_unmeasured"] == ["completion_rate", "watch_time_s", "shares"]
    assert "4200" in out["echo"] and "NULL, not zero" in out["echo"]
    session.expire_all()
    assert session.get(Metric, (pid, "tiktok", TARGET)) is None, "nothing written before confirm"


def test_confirming_writes_exactly_what_was_echoed(session, tools, engine):
    pid = _published(session, "post_9202")
    line = "4200, , , 45, , 118"
    preview = call(tools, "record_metrics", engine, post_id=pid, channel="tiktok",
                   metric_date=str(TARGET), figures_line=line, operator_message=line)
    out = call(tools, "record_metrics", engine, post_id=pid, channel="tiktok",
               metric_date=str(TARGET), figures_line=line, operator_message=line, confirm=True)
    assert out["written"] is True
    assert out["echo"] == preview["echo"], "what was confirmed is what was written"

    session.expire_all()
    row = session.get(Metric, (pid, "tiktok", TARGET))
    assert row.impressions == 4200 and row.saves == 45 and row.clicks == 118
    assert row.completion_rate is None and row.watch_time_s is None and row.shares is None
    assert row.operator_message == line
    assert row.source == "operator_via_agent"


def test_the_hook_polices_an_ordered_line_exactly_like_a_figures_dict(session, tools, engine):
    verdict = tools.pre_tool_call("record_metrics", args={
        "post_id": "post_9203", "channel": "tiktok", "metric_date": str(TARGET),
        "figures_line": "4200, , , 45, , 118",
        "operator_message": "4200 impressions and 45 saves",     # 118 never typed
    })
    assert verdict and verdict["action"] == "block"
    assert "clicks=118" in verdict["message"]


def test_the_hook_passes_a_line_the_operator_actually_typed(session, tools, engine):
    assert tools.pre_tool_call("record_metrics", args={
        "post_id": "post_9204", "channel": "tiktok", "metric_date": str(TARGET),
        "figures_line": "4200, , , 45, , 118",
        "operator_message": "4200, , , 45, , 118",
    }) is None
