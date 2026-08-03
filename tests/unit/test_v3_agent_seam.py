"""v3 acceptance 12–16, 18, 19 — the six-tool seam on the DOCUMENTED handler contract:
(args: dict, **kwargs) -> str, JSON always, never raise.
Contract source: https://hermes-agent.nousresearch.com/docs/developer-guide/plugins
"""

import importlib.util
import json
from datetime import date

import pytest
from sqlalchemy import func, select

from app.config import set_config
from app.models import Asset, Learning, Order, PlanShadow, Post, Run

WEEK = date(2026, 8, 10)


@pytest.fixture
def tools(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_plugin_tools", repo_root / "plugins" / "artec" / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(tools, name, engine, **args):
    """Invoke a handler the way the agent does and unwrap the JSON envelope."""
    raw = tools.HANDLERS[name](args, engine=engine)
    assert isinstance(raw, str), f"{name} must return a JSON STRING, got {type(raw)}"
    body = json.loads(raw)
    assert body["ok"] is True, f"{name} errored: {body.get('error')}"
    return body["data"]


PLAN = [
    {"channel": "instagram", "angle": "focus", "hook": "H1", "cta_type": "discount",
     "cta_placement": "caption_end", "keywords": ["stem"], "slot": "evening"},
    {"channel": "tiktok", "angle": "speed", "hook": "H2", "cta_type": "learn_more",
     "cta_placement": "caption_start", "keywords": [], "slot": "lunch"},
]


def test_fifteen_handlers_and_none_touch_orders_events_or_config(tools, repo_root):
    # v4 acceptance 1 + 2. The security property was never the COUNT — it is the absence of
    # capability. orders/events/config stay unreachable; metrics became writable by
    # TRANSCRIPTION ONLY (enforced by the pre_tool_call hook, tested in test_v4_seam.py).
    assert len(tools.HANDLERS) == 15
    assert set(tools.HANDLERS) == {
        "read_brief", "read_learnings", "read_asset_inventory", "read_parked_posts",
        "read_draft_posts", "read_digest", "write_plan", "record_gate_decision",
        "deliver_video", "review_video", "review_email", "record_metrics", "retry_post",
        "fulfil_wishlist", "acknowledge_price_table",
    }
    seam = "\n".join(
        (repo_root / "plugins" / "artec" / name).read_text(encoding="utf-8")
        for name in ("tools.py", "tools_v4.py")
    )
    for table in ("orders", "events"):
        for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"):
            assert f"{verb} {table}" not in seam, f"the seam must never {verb} {table}"
    # config is readable but never writable — except the post_id counter, which is an id
    # allocator rather than policy, and is the only permitted exception.
    config_writes = [line.strip() for line in seam.splitlines()
                     if "UPDATE config" in line or "INSERT INTO config" in line]
    assert all("post_id_counter" in line for line in config_writes), \
        f"unexpected config write in the seam: {config_writes}"


def test_handlers_return_json_string_and_never_raise(tools, engine):
    # The documented contract, exercised with garbage input: error JSON, no exception.
    raw = tools.HANDLERS["write_plan"]({"week_start": "not-a-date", "posts": "garbage"},
                                       engine=engine)
    body = json.loads(raw)
    assert body["ok"] is False and "error" in body
    raw = tools.HANDLERS["read_learnings"]({}, engine=engine)  # missing required arg
    body = json.loads(raw)
    assert body["ok"] is False
    raw = tools.HANDLERS["record_gate_decision"](
        {"post_id": "x", "action": "detonate"}, engine=engine)
    body = json.loads(raw)
    assert body["ok"] is False and "unknown gate action" in body["error"]


def test_writing_an_order_is_no_such_tool_not_permission(tools, engine):
    # acceptance 13
    with pytest.raises(LookupError, match="no such tool"):
        tools.dispatch("write_order", amount=100, engine=engine)
    with pytest.raises(LookupError, match="no such tool"):
        tools.dispatch("sql", query="DELETE FROM orders", engine=engine)


def test_shadow_mode_writes_only_plans_shadow(tools, session, engine):
    # acceptance 16
    out = call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)
    assert out["plan_source"] == "shadow"
    assert out["post_ids"] == [] and out["shadow_rows"] == 2
    assert session.execute(select(func.count()).select_from(Post)).scalar() == 0
    rows = list(session.execute(select(PlanShadow)).scalars())
    assert len(rows) == 2 and all(r.source == "agent" for r in rows)
    logged = [r for r in session.execute(select(Run)).scalars()
              if r.command == "agent-tool write_plan"]
    assert logged and logged[0].args["posts"] == 2


def test_write_plan_idempotent_on_week_channel_slot(tools, session, engine):
    call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)
    out = call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)  # acceptance 14
    assert out["shadow_rows"] == 0
    assert session.execute(select(func.count()).select_from(PlanShadow)).scalar() == 2


def test_agent_mode_writes_drafts_and_mirrors_shadow(tools, session, engine):
    set_config(session, "plan_source", "agent")
    session.commit()
    out = call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)
    assert len(out["post_ids"]) == 2
    drafts = list(session.execute(select(Post)).scalars())
    assert all(p.status == "DRAFT" and p.plan_source == "agent" for p in drafts)
    assert all(p.tracked_url and f"utm_campaign={p.post_id}" in p.tracked_url for p in drafts)
    out2 = call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)
    assert out2["post_ids"] == []


def test_bespoke_flip_disables_agent_path_no_redeploy(tools, session, engine):
    # acceptance 18
    set_config(session, "plan_source", "bespoke")
    session.commit()
    out = call(tools, "write_plan", engine, week_start=str(WEEK), posts=PLAN)
    assert out == {"disabled": True, "plan_source": "bespoke", "written": 0}
    assert session.execute(select(func.count()).select_from(PlanShadow)).scalar() == 0


def test_gate_decisions_idempotent_and_reject_shrinks_week(tools, session, engine):
    for i, pid in enumerate(["post_8001", "post_8002"]):
        session.add(Post(post_id=pid, week_start=WEEK, channel="instagram",
                         status="DRAFT", hook=f"H{i}", slot="evening"))
    session.commit()

    out = call(tools, "record_gate_decision", engine, post_id="post_8001", action="approve")
    assert out["status"] == "APPROVED"
    out = call(tools, "record_gate_decision", engine, post_id="post_8001", action="reject")
    assert out["already"] == "approve"  # acceptance 14: first decision stands
    assert session.get(Post, "post_8001").status == "APPROVED"

    call(tools, "record_gate_decision", engine, post_id="post_8002", action="reject")
    session.expire_all()
    assert session.get(Post, "post_8002").status == "REJECTED"
    # acceptance 15: no replacement post — the week's live count decreases.
    assert session.execute(select(func.count()).select_from(Post)).scalar() == 2
    live = session.execute(select(func.count()).select_from(Post)
                           .where(Post.status != "REJECTED")).scalar()
    assert live == 1


def test_gate_edit_applies_deltas_and_stores_them(tools, session, engine):
    session.add(Post(post_id="post_8003", week_start=WEEK, channel="instagram",
                     status="DRAFT", hook="old hook", slot="evening"))
    session.commit()
    call(tools, "record_gate_decision", engine, post_id="post_8003", action="edit",
         edits={"hook": "sharper hook"})
    session.expire_all()
    p = session.get(Post, "post_8003")
    assert p.status == "APPROVED" and p.hook == "sharper hook"
    assert p.gate_action["action"] == "edit"
    assert p.gate_action["edits"] == {"hook": "sharper hook"}  # the deltas train taste


def test_read_brief_lanes_separate_never_blended(tools, session, engine):
    # acceptance 19
    session.add(Post(post_id="post_8010", week_start=WEEK, channel="instagram",
                     status="PUBLISHED", hook="h"))
    session.add(Order(source="stripe", external_id="cs_x", post_id="post_8010",
                      amount_minor=13900, currency="SGD"))
    session.add(Asset(drive_file_id="ax", drive_path="raw-photo/x.jpg", medium="photo",
                      subject="loose_blocks", status="active"))
    session.commit()
    brief = call(tools, "read_brief", engine)
    assert "== REVENUE (orders only" in brief
    assert "== ENGAGEMENT (events + metrics only" in brief
    revenue_block = brief.split("== ENGAGEMENT")[0].split("== REVENUE")[1]
    engagement_block = brief.split("== ENGAGEMENT")[1]
    assert "impressions" not in revenue_block
    assert "minor units" not in engagement_block
    assert "unmeasured" in engagement_block
    for blend_word in ("combined", "blended", "total score"):
        assert blend_word not in brief.lower()


def test_read_only_tools_read(tools, session, engine):
    session.add(Learning(week_start=WEEK, lever="hook", lever_value="H1",
                         kpi="weighted", score=0.7, sample_size=3, verdict="keep"))
    session.add(Asset(drive_file_id="a1", drive_path="raw-photo/assembled/a.jpg",
                      medium="photo", subject="assembled_blocks", status="active"))
    session.add(Post(post_id="post_8020", week_start=WEEK, channel="tiktok",
                     status="PARKED", hook="h",
                     asset_wishlist=[{"target_folder": "raw-video", "medium": "video",
                                      "description": "d"}]))
    session.commit()
    learnings = call(tools, "read_learnings", engine, week_start=str(WEEK))
    assert learnings[0]["verdict"] == "keep"
    inv = call(tools, "read_asset_inventory", engine)
    assert {"subject": "assembled_blocks", "medium": "photo", "count": 1, "unused": 1} in inv
    parked = call(tools, "read_parked_posts", engine)
    assert parked[0]["post_id"] == "post_8020"
    assert parked[0]["asset_wishlist"][0]["target_folder"] == "raw-video"
