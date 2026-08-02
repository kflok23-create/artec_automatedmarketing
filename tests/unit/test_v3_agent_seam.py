"""v3 acceptance 12–16, 18, 19 — the six-tool seam, exercised directly against the DB
(the plugin is self-contained; tests inject the fixture engine)."""

import importlib.util
from datetime import date

import pytest
from sqlalchemy import func, select

from app.config import set_config
from app.models import Asset, Learning, Order, PlanShadow, Post, Run

WEEK = date(2026, 8, 10)


@pytest.fixture
def plugin(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artec_hermes", repo_root / "plugins" / "artec_hermes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PLAN = [
    {"channel": "instagram", "angle": "focus", "hook": "H1", "cta_type": "discount",
     "cta_placement": "caption_end", "keywords": ["stem"], "slot": "evening"},
    {"channel": "tiktok", "angle": "speed", "hook": "H2", "cta_type": "learn_more",
     "cta_placement": "caption_start", "keywords": [], "slot": "lunch"},
]


def test_exactly_six_tools_none_touch_money(plugin):
    # acceptance 12: six tools; no capability writes orders/events/metrics/config.
    assert len(plugin.TOOLS) == 6
    assert set(plugin.TOOLS) == {"read_brief", "read_learnings", "read_asset_inventory",
                                 "read_parked_posts", "write_plan", "record_gate_decision"}
    import inspect

    for name in ("write_plan", "record_gate_decision"):
        src = inspect.getsource(plugin.TOOLS[name])
        for table in ("INSERT INTO orders", "INSERT INTO events", "INSERT INTO metrics",
                      "UPDATE orders", "UPDATE events", "UPDATE metrics",
                      "DELETE FROM orders", "DELETE FROM events", "DELETE FROM metrics"):
            assert table not in src, f"{name} must never touch {table.split()[-1]}"


def test_writing_an_order_is_no_such_tool_not_permission(plugin, engine):
    # acceptance 13: the capability does not exist — LookupError, not PermissionError.
    with pytest.raises(LookupError, match="no such tool"):
        plugin.dispatch("write_order", amount=100, engine=engine)
    with pytest.raises(LookupError, match="no such tool"):
        plugin.dispatch("sql", query="DELETE FROM orders", engine=engine)


def test_shadow_mode_writes_only_plans_shadow(plugin, session, engine):
    # acceptance 16: default shadow — agent output never lands in posts.
    out = plugin.write_plan(str(WEEK), PLAN, engine=engine)
    assert out["plan_source"] == "shadow"
    assert out["post_ids"] == [] and out["shadow_rows"] == 2
    assert session.execute(select(func.count()).select_from(Post)).scalar() == 0
    rows = list(session.execute(select(PlanShadow)).scalars())
    assert len(rows) == 2 and all(r.source == "agent" for r in rows)
    # tool calls log to runs with their arguments
    logged = [r for r in session.execute(select(Run)).scalars()
              if r.command == "agent-tool write_plan"]
    assert logged and logged[0].args["posts"] == 2


def test_write_plan_idempotent_on_week_channel_slot(plugin, session, engine):
    plugin.write_plan(str(WEEK), PLAN, engine=engine)
    out = plugin.write_plan(str(WEEK), PLAN, engine=engine)   # acceptance 14
    assert out["shadow_rows"] == 0
    assert session.execute(select(func.count()).select_from(PlanShadow)).scalar() == 2


def test_agent_mode_writes_drafts_and_mirrors_shadow(plugin, session, engine):
    set_config(session, "plan_source", "agent")
    session.commit()
    out = plugin.write_plan(str(WEEK), PLAN, engine=engine)
    assert len(out["post_ids"]) == 2
    drafts = list(session.execute(select(Post)).scalars())
    assert all(p.status == "DRAFT" and p.plan_source == "agent" for p in drafts)
    assert all(p.tracked_url and f"utm_campaign={p.post_id}" in p.tracked_url for p in drafts)
    # idempotent in agent mode too
    out2 = plugin.write_plan(str(WEEK), PLAN, engine=engine)
    assert out2["post_ids"] == []


def test_bespoke_flip_disables_agent_path_no_redeploy(plugin, session, engine):
    # acceptance 18: rollback is one config row.
    set_config(session, "plan_source", "bespoke")
    session.commit()
    out = plugin.write_plan(str(WEEK), PLAN, engine=engine)
    assert out == {"disabled": True, "plan_source": "bespoke", "written": 0}
    assert session.execute(select(func.count()).select_from(PlanShadow)).scalar() == 0


def test_gate_decisions_idempotent_and_reject_shrinks_week(plugin, session, engine):
    for i, pid in enumerate(["post_8001", "post_8002"]):
        session.add(Post(post_id=pid, week_start=WEEK, channel="instagram",
                         status="DRAFT", hook=f"H{i}", slot="evening"))
    session.commit()

    out = plugin.record_gate_decision("post_8001", "approve", engine=engine)
    assert out["status"] == "APPROVED"
    out = plugin.record_gate_decision("post_8001", "reject", engine=engine)  # acceptance 14
    assert out["already"] == "approve"  # first decision stands — idempotent on post_id
    assert session.get(Post, "post_8001").status == "APPROVED"

    plugin.record_gate_decision("post_8002", "reject", engine=engine)
    session.expire_all()
    p2 = session.get(Post, "post_8002")
    assert p2.status == "REJECTED"
    # acceptance 15: no replacement post appears — the week's live count decreases.
    assert session.execute(select(func.count()).select_from(Post)).scalar() == 2
    live = session.execute(select(func.count()).select_from(Post)
                           .where(Post.status != "REJECTED")).scalar()
    assert live == 1


def test_gate_edit_applies_deltas_and_stores_them(plugin, session, engine):
    session.add(Post(post_id="post_8003", week_start=WEEK, channel="instagram",
                     status="DRAFT", hook="old hook", slot="evening"))
    session.commit()
    plugin.record_gate_decision("post_8003", "edit", {"hook": "sharper hook"}, engine=engine)
    session.expire_all()
    p = session.get(Post, "post_8003")
    assert p.status == "APPROVED" and p.hook == "sharper hook"
    assert p.gate_action["action"] == "edit"
    assert p.gate_action["edits"] == {"hook": "sharper hook"}  # the deltas train taste


def test_read_brief_lanes_separate_never_blended(plugin, session, engine):
    # acceptance 19
    session.add(Post(post_id="post_8010", week_start=WEEK, channel="instagram",
                     status="PUBLISHED", hook="h"))
    session.add(Order(source="stripe", external_id="cs_x", post_id="post_8010",
                      amount_minor=13900, currency="SGD"))
    session.add(Asset(drive_file_id="ax", drive_path="raw-photo/x.jpg", medium="photo",
                      subject="loose_blocks", status="active"))
    session.commit()
    brief = plugin.read_brief(engine=engine)
    assert "== REVENUE (orders only" in brief
    assert "== ENGAGEMENT (events + metrics only" in brief
    revenue_block = brief.split("== ENGAGEMENT")[0].split("== REVENUE")[1]
    engagement_block = brief.split("== ENGAGEMENT")[1]
    assert "impressions" not in revenue_block
    assert "minor units" not in engagement_block
    assert "unmeasured" in engagement_block  # stale ≠ zero, labelled in the text
    for blend_word in ("combined", "blended", "total score"):
        assert blend_word not in brief.lower()


def test_read_only_tools_read(plugin, session, engine):
    session.add(Learning(week_start=WEEK, lever="hook", lever_value="H1",
                         kpi="weighted", score=0.7, sample_size=3, verdict="keep"))
    session.add(Asset(drive_file_id="a1", drive_path="raw-photo/assembled/a.jpg",
                      medium="photo", subject="assembled_blocks", status="active"))
    session.add(Post(post_id="post_8020", week_start=WEEK, channel="tiktok",
                     status="PARKED", hook="h",
                     asset_wishlist=[{"target_folder": "raw-video", "medium": "video",
                                      "description": "d"}]))
    session.commit()
    assert plugin.read_learnings(str(WEEK), engine=engine)[0]["verdict"] == "keep"
    inv = plugin.read_asset_inventory(engine=engine)
    assert {"subject": "assembled_blocks", "medium": "photo", "count": 1, "unused": 1} in inv
    parked = plugin.read_parked_posts(engine=engine)
    assert parked[0]["post_id"] == "post_8020"
    assert parked[0]["asset_wishlist"][0]["target_folder"] == "raw-video"
