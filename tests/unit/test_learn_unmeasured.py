"""INVARIANT 2, AT THE PLACE IT WAS BREACHED: unmeasured is not zero.

The fixture here is week 2026-08-03's REAL shape, taken from production: five PUBLISHED
posts, zero `metrics` rows, two of them withdrawn after publishing to the wrong surface.
Sunday 2026-08-09 at 06:00 runs `learn` against exactly this.

WHAT IT PRODUCED BEFORE THE FIX — six levers, every score zero, every verdict real:

    lever=angle        value=build        score=0E-10 n=3 verdict=test
    lever=channel      value=instagram    score=0E-10 n=2 verdict=test
    lever=channel      value=tiktok       score=0E-10 n=1 verdict=test
    lever=cta_type     value=shop         score=0E-10 n=3 verdict=test
    lever=hook         value=time         score=0E-10 n=3 verdict=test
    lever=slot         value=lunch        score=0E-10 n=3 verdict=test

Three defaults produced that, each reasonable alone: `eng_n.get(value, 0.0)` for absent
engagement, `traffic[value] = 0.0` for no events, `sales_score = 0.0` for no orders.
Together they assert "these levers performed at the bottom of the range" about a week nobody
measured — and those verdicts feed IDEATE, so next week's plan would have been shaped by it.

The system already knew how to say this honestly: `channel_cac` reports
"unmeasurable (no spend configured)" rather than scoring a zero CAC. The weighted levers
simply did not.

WHAT SUPPLIES EACH SIDE: the posts and the absence of metrics are inserted by the test; the
verdicts come from `learn`'s SQL-side arithmetic. No LLM is involved — the governing rule is
that the agent does interpretation and SQL does arithmetic, so the scoring is testable
without a model. That is the route that makes this closable at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import Learning, Metric, Post
from app.stages.learn import learn

WEEK = date(2026, 8, 3)

# Exactly what production holds for that week.
BOARD = (
    ("post_1483", "instagram", None),
    ("post_1484", "instagram", None),
    ("post_1486", "tiktok", None),
    ("post_1488", "facebook", datetime(2026, 8, 5, tzinfo=UTC)),
    ("post_1489", "linkedin", datetime(2026, 8, 5, tzinfo=UTC)),
)


@pytest.fixture
def week_2026_08_03(session):
    for post_id, channel, withdrawn in BOARD:
        session.add(Post(
            post_id=post_id, channel=channel, week_start=WEEK, status="PUBLISHED",
            slot="lunch", angle="build", hook="time", cta_type="shop",
            external_post_id=f"ext_{post_id}",
            posted_at=datetime(2026, 8, 4, tzinfo=UTC), withdrawn_at=withdrawn))
    session.flush()
    return session


def _verdicts(session):
    return list(session.execute(
        select(Learning).where(Learning.week_start == WEEK)).scalars())


def test_no_lever_is_scored_from_absent_measurement(week_2026_08_03):
    """THE BREACH. Every weighted verdict must have real evidence behind it."""
    learn(week_2026_08_03, llm=None, week_start=WEEK, log=lambda *a: None)

    scored = [r for r in _verdicts(week_2026_08_03) if r.score is not None]
    assert scored == [], (
        "learn produced scored verdicts for a week with no metrics, no events and no "
        f"orders: {[(r.lever, r.lever_value, r.score, r.verdict) for r in scored]}. "
        "Unmeasured is not zero — a zero score is a claim that the lever underperformed, "
        "and IDEATE reads these.")


def test_the_unmeasured_posts_are_named_not_merely_absent(week_2026_08_03):
    """Excluding silently would be a different defect: the operator must see WHICH posts
    produced no evidence, or 'insufficient data' looks like nothing was published."""
    learn(week_2026_08_03, llm=None, week_start=WEEK, log=lambda *a: None)

    rows = {r.lever: r for r in _verdicts(week_2026_08_03)}
    assert "unmeasured" in rows, "no row names the unmeasured posts"
    unmeasured = rows["unmeasured"]
    assert unmeasured.score is None
    assert unmeasured.sample_size == 3          # the two withdrawn are excluded earlier
    for post_id in ("post_1483", "post_1484", "post_1486"):
        assert post_id in unmeasured.verdict
    # The withdrawn pair must NOT be counted as unmeasured — different fact, different fix.
    assert "post_1488" not in unmeasured.verdict
    assert "post_1489" not in unmeasured.verdict


def test_insufficient_data_says_why_rather_than_implying_nothing_published(week_2026_08_03):
    """Cold start and all-unmeasured reach the same conclusion by different routes, and the
    route matters: one means nothing went out, the other means nobody measured what did."""
    learn(week_2026_08_03, llm=None, week_start=WEEK, log=lambda *a: None)

    cold = next(r for r in _verdicts(week_2026_08_03) if r.lever == "cold_start")
    assert "published but NONE carries any measurement" in cold.verdict


def test_a_post_with_ANY_evidence_is_still_scored(week_2026_08_03):
    """The exclusion must not swallow real data. One metrics row is enough to be measured —
    evidence in any lane counts, because the lanes are separate for COMBINATION, not for
    existence."""
    week_2026_08_03.add(Metric(
        post_id="post_1483", channel="instagram", metric_date=WEEK,
        impressions=4200, clicks=118, saves=45))
    week_2026_08_03.flush()

    learn(week_2026_08_03, llm=None, week_start=WEEK, log=lambda *a: None)

    scored = [r for r in _verdicts(week_2026_08_03) if r.score is not None]
    assert scored, "a post with a real metrics row must still produce scored levers"
    assert {r.lever for r in scored} & {"channel", "angle", "hook", "slot", "cta_type"}

    rows = {r.lever: r for r in _verdicts(week_2026_08_03)}
    # The other two remain unmeasured and are still named.
    assert "post_1484" in rows["unmeasured"].verdict
    assert "post_1483" not in rows["unmeasured"].verdict


def test_withdrawn_and_unmeasured_are_different_findings(week_2026_08_03):
    """Both are excluded from scoring and neither is zero — but conflating them would hide
    that two channels were removed at source for a targeting defect."""
    logged: list[str] = []
    learn(week_2026_08_03, llm=None, week_start=WEEK, log=lambda m: logged.append(str(m)))
    joined = " ".join(logged)

    assert "2 withdrawn post(s)" in joined
    assert "3 unmeasured post(s)" in joined
    assert "post_1488" in joined and "post_1483" in joined
