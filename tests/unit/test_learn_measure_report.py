"""Acceptance 18 (lanes never blended), 19 (unmeasured ≠ 0), 20 (cold start), plus
measure NULL semantics and ideate cadence."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.config import OperatorError, set_config
from app.integrations.fakes import FakeLLM
from app.models import Learning, Metric, Order, Post
from app.stages.ideate import ideate
from app.stages.learn import learn
from app.stages.measure import measure_json
from app.stages.report import build_report

WEEK = date(2026, 7, 27)


def test_learn_cold_start_writes_insufficient_data(session):
    out = learn(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)   # 20
    assert out["cold_start"] is True
    row = session.execute(select(Learning)).scalar_one()
    assert row.verdict == "insufficient data"


def test_learn_scores_and_is_idempotent(session):
    for i, ch in enumerate(["instagram", "tiktok"]):
        session.add(Post(post_id=f"post_{1600 + i}", week_start=WEEK, channel=ch,
                         status="PUBLISHED", angle="a", hook=f"h{i}", cta_type="discount",
                         cta_placement="caption_end", slot="evening"))
    session.add(Metric(post_id="post_1600", channel="instagram", metric_date=WEEK,
                       impressions=1000, saves=10, shares=2, clicks=5))
    session.add(Order(source="stripe", external_id="cs1", post_id="post_1600",
                      amount_minor=13900, currency="SGD", occurred_at=datetime.now(UTC)))
    session.flush()
    learn(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)
    first = len(list(session.execute(select(Learning)).scalars()))
    learn(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)
    second = len(list(session.execute(select(Learning)).scalars()))
    assert first == second > 0  # re-run replaces, never accumulates


def test_measure_blank_stays_null_never_zero(session):
    session.add(Post(post_id="post_1700", week_start=WEEK, channel="tiktok", status="PUBLISHED"))
    session.flush()
    measure_json(session, {"rows": [{"post_id": "post_1700", "channel": "tiktok",
                                     "metric_date": str(WEEK), "impressions": 500}]},
                 log=lambda *_: None)
    m = session.get(Metric, ("post_1700", "tiktok", WEEK))
    assert m.impressions == 500
    assert m.clicks is None and m.saves is None        # skipped fields stay NULL
    # Upsert on the natural key: a second entry updates, never duplicates.
    measure_json(session, {"rows": [{"post_id": "post_1700", "channel": "tiktok",
                                     "metric_date": str(WEEK), "clicks": 7}]},
                 log=lambda *_: None)
    m = session.get(Metric, ("post_1700", "tiktok", WEEK))
    assert m.impressions == 500 and m.clicks == 7


def test_report_lanes_are_separate_and_unmeasured_is_named(session):
    session.add(Post(post_id="post_1800", week_start=WEEK, channel="instagram", status="PUBLISHED"))
    session.add(Post(post_id="post_1801", week_start=WEEK, channel="tiktok", status="PUBLISHED"))
    session.add(Order(source="stripe", external_id="cs9", post_id="post_1800",
                      amount_minor=13900, currency="SGD", occurred_at=datetime.now(UTC)))
    session.add(Order(source="billplz", external_id="b9", post_id=None,
                      amount_minor=44900, currency="MYR", occurred_at=datetime.now(UTC)))
    session.add(Metric(post_id="post_1800", channel="instagram", metric_date=WEEK, impressions=100))
    session.flush()

    report = build_report(session, week_start=WEEK)
    # 18: separate blocks, no shared keys, no money inside engagement and vice versa.
    assert set(report["revenue"]).isdisjoint(set(report["engagement"]))
    assert "net_cm_minor" not in str(report["engagement"])
    assert "impressions" not in str(report["revenue"])
    assert report["revenue"]["by_currency"]["SGD"]["net_cm_minor"] == 7400  # NET, not gross
    assert report["revenue"]["unattributed"]["orders"] == 1
    # 19: post_1801 has no metrics row → "unmeasured", never 0.
    assert report["unmeasured"] == ["post_1801"]
    assert "post_1801" not in report["engagement"]["metrics_by_post"]


def test_ideate_requires_seeds_and_respects_cadence(session):
    set_config(session, "seo_seeds", [])
    with pytest.raises(OperatorError, match="SEO_SEEDS"):
        ideate(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)

    set_config(session, "seo_seeds", ["s1", "s2", "s3", "s4", "s5"])
    out = ideate(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)
    assert out["created"] == 9  # 3+2+1+1+1+1 per default cadence
    drafts = list(session.execute(select(Post).where(Post.status == "DRAFT")).scalars())
    assert len(drafts) == 9
    by_channel = {}
    for d in drafts:
        by_channel[d.channel] = by_channel.get(d.channel, 0) + 1
        assert d.tracked_url and d.utm["utm_campaign"] == d.post_id
    assert by_channel == {"instagram": 3, "tiktok": 2, "linkedin": 1, "facebook": 1,
                          "youtube": 1, "email": 1}
    # Idempotent: a re-run tops up nothing.
    out2 = ideate(session, FakeLLM(), week_start=WEEK, log=lambda *_: None)
    assert out2["created"] == 0
