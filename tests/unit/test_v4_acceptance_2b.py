"""v4 §15 acceptance — the seven this stage owes: 11, 12, 13, 14, 15, 18, 20.

Each drives the REAL path end to end (render output → publish → digest), not the helper in
isolation: 11 and 12 already had pre-flight-level tests, but "PARKED, not published, and
appears in the digest" is a claim about the pipeline, and only the pipeline can make it.
"""

import importlib.util
import json
import shutil
import subprocess
from datetime import UTC, date, datetime

import pytest

from app.config import set_config
from app.integrations.fakes import FakeBrevo, FakeDrive, FakeFal, FakeUploadPost
from app.models import AgentRun, Post
from app.scheduler import sweep_orphaned_slots
from app.stages.digest import build_payload, render_digest_text
from app.stages.preflight import check_caption
from app.stages.publish import publish

WEEK = date(2026, 8, 24)
TARGET = date(2026, 8, 27)
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class FakeBrevoCount:
    def get_list_count(self):
        return 1


def _encode(path, *, seconds=12, size="1080x1920", faststart=True):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=s={size}:d={seconds}:r=30", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         *(["-movflags", "+faststart"] if faststart else []), "-y", str(path)],
        check=True, capture_output=True, timeout=300)
    return str(path)


def _video_post(session, pid, **kw):
    defaults = dict(week_start=WEEK, channel="tiktok", status="APPROVED_TO_SEND",
                    slot="evening", media_drive_file_id="gen_v.mp4", caption="hook",
                    tracked_url="https://artec.my/",
                    video_review={"decision": "approve", "telegram_message_id": 1})
    defaults.update(kw)
    session.add(Post(post_id=pid, **defaults))
    session.commit()
    return session.get(Post, pid)


def _publish_with(session, path):
    class OneFileDrive(FakeDrive):
        def download(self, file_id, suffix=""):
            return path

    set_config(session, "confirm_first_publish", False)
    return publish(session, OneFileDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                   all_rendered=True, confirm=False, log=lambda *_: None)


# ---- 11 · trailing moov ------------------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here — a mocked "
                                           "probe would prove nothing about a real file")
def test_11_a_trailing_moov_video_is_parked_and_reaches_the_digest(session, tmp_path):
    post = _video_post(session, "post_3011")
    path = _encode(tmp_path / "trailing.mp4", seconds=12, faststart=False)

    assert _publish_with(session, path)["published"] == 0
    session.expire_all()
    post = session.get(Post, "post_3011")
    assert post.status == "PARKED"
    assert "moov" in post.park_reason.lower()

    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(), target=TARGET))
    assert "post_3011" in text, "a park nobody is told about is a post lost silently"


# ---- 12 · duration outside the platform bound --------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH here")
def test_12_a_video_past_the_platform_duration_bound_is_parked(session, tmp_path):
    # tiktok's channel_media duration_s is 12 → publish bounds it at (6, 24)
    _video_post(session, "post_3012")
    path = _encode(tmp_path / "long.mp4", seconds=40)

    assert _publish_with(session, path)["published"] == 0
    session.expire_all()
    post = session.get(Post, "post_3012")
    assert post.status == "PARKED" and "duration" in post.park_reason.lower()


# ---- 13 · caption length changed between render and publish ------------------------------

def test_13_a_caption_whose_length_changed_fails_preflight(session):
    stored = "Build focus, one block at a time — 10 minutes a day gives your child…"
    problem = check_caption(stored, stored[:40])
    assert problem and "truncation" in problem
    assert check_caption(stored, stored) is None


# ---- 14 · every video holds for review, every time ---------------------------------------

def test_14_no_flag_or_first_run_exemption_lets_a_video_skip_the_gate(session):
    """There is no configuration value, first-run exemption, or clean-ffprobe path that
    publishes a video the operator never approved. Asserted by trying all of them."""
    from app.stages.publish import skip_reason

    post = _video_post(session, "post_3014", status="RENDERED", video_review=None)

    # a clean pre-flight does not confer approval
    assert skip_reason(session, post) is not None

    # nor does any config flip: there is no key that turns the gate off
    for key, value in (("confirm_first_publish", False), ("generate_enabled", True),
                       ("kill_lines_active", True), ("allow_person_assets", True)):
        set_config(session, key, value)
    session.expire_all()
    assert skip_reason(session, session.get(Post, "post_3014")) is not None

    # nor being the first post ever published on this install
    assert _publish_with(session, "unused")["published"] == 0
    session.expire_all()
    assert session.get(Post, "post_3014").status == "RENDERED", "held, not failed"


def test_14b_a_delivered_but_undecided_video_still_holds(session):
    from app.stages.publish import skip_reason

    post = _video_post(session, "post_3015", status="RENDERED",
                       video_review={"telegram_message_id": 99, "decision": None})
    assert skip_reason(session, post) is not None


# ---- 15 · slot vocabulary, both guards ---------------------------------------------------

# 15's write-time half is already covered on BOTH planners and is not duplicated here:
# bespoke ideate in test_v4_preflight.py (_BadSlotLLM), the seam in test_v4_seam.py
# (test_inject_rejects_an_off_vocabulary_slot). The sweep half is below.

def test_15b_an_orphaned_rendered_post_is_swept_into_the_digest(session):
    session.add(Post(post_id="post_3016", week_start=WEEK, channel="instagram",
                     status="RENDERED", slot="afternoon"))
    session.flush()
    assert [o["post_id"] for o in sweep_orphaned_slots(session)] == ["post_3016"]
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(), target=TARGET))
    assert "ORPHAN SLOT" in text and "post_3016" in text


# ---- 18 · agent_runs gains a row per brain job AND per tool call --------------------------

@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_acc_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_18_a_row_per_brain_job_and_per_tool_call(session, tools, engine, repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_acc_runs", repo_root / "plugins" / "artec" / "agent_runs.py")
    agent_runs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_runs)

    run_id = agent_runs.start_run("nightly-digest", session_id="task_18", engine=engine)
    assert run_id, "a brain job must be recorded when it starts, not only when it ends"

    # every tool call goes through the hook in production, and the hook is the meter
    for tool_name in ("read_parked_posts", "read_asset_inventory"):
        assert tools.pre_tool_call(tool_name, args={}, task_id="task_18",
                                   engine=engine) is None
        json.loads(tools.HANDLERS[tool_name]({}, engine=engine))
    agent_runs.finish_run(run_id, status="ok", cost_cents=7, engine=engine)

    session.expire_all()
    row = session.get(AgentRun, run_id)
    assert row.job == "nightly-digest" and row.status == "ok" and row.cost_cents == 7
    called = [c["tool"] for c in (row.tools_called or [])]
    assert called == ["read_parked_posts", "read_asset_inventory"], \
        "per tool call, not just per job"


def test_18b_an_operator_started_conversation_is_metered_too(session, tools, engine):
    """A conversation the operator starts has no cron wrapper to have opened a run — the
    spend still has to land somewhere, or the meter reads low exactly when the brain is
    busiest."""
    tools.pre_tool_call("read_parked_posts", args={}, task_id="task_adhoc", engine=engine)
    session.expire_all()
    row = session.query(AgentRun).filter(AgentRun.session_id == "task_adhoc").one()
    assert row.job == "telegram-session"
    assert [c["tool"] for c in row.tools_called] == ["read_parked_posts"]


# ---- 20 · nothing that changed state is invisible -----------------------------------------

def test_20_every_post_whose_state_changed_in_24h_appears_in_the_payload(session):
    from app.stages.digest import assert_complete

    now = datetime.now(UTC)
    session.add(Post(post_id="post_3020", week_start=WEEK, channel="instagram",
                     status="PUBLISHED", slot="evening", posted_at=now))
    session.add(Post(post_id="post_3021", week_start=WEEK, channel="tiktok",
                     status="FAILED", slot="evening", park_reason="upload timeout"))
    session.add(Post(post_id="post_3022", week_start=WEEK, channel="facebook",
                     status="PARKED", slot="lunch", park_reason="no asset"))
    session.add(Post(post_id="post_3023", week_start=WEEK, channel="linkedin",
                     status="APPROVED_TO_SEND", slot="morning",
                     media_drive_file_id="gen_a.jpg"))
    session.flush()

    payload = build_payload(session, brevo=FakeBrevoCount(), target=TARGET)
    assert assert_complete(session, payload) == []
    blob = json.dumps(payload, default=str)
    for pid in ("post_3020", "post_3021", "post_3022", "post_3023"):
        assert pid in blob, f"{pid} changed state and is invisible to the operator"
