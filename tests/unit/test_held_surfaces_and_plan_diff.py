"""§1 · The three probes, as tests against the DEPLOYED predicates.

The rule this build runs on is that a guard which exists is not a guard which applies. Five
guards so far have been correct in isolation and connected to nothing. So each of these
asserts the predicate that PUBLISH ACTUALLY CALLS, not a parallel restatement of it.

WHAT SUPPLIES EACH SIDE, per probe:
  email/video  the post's state is written by the test; the refusal comes from
               `skip_reason`, reached through `publish()` itself at publish.py:195 —
               the same call the scheduler's job 7 makes. Neither side is a copy.
  receipt      `telegram_message_id` is read out of Telegram's own API response inside
               `deliver_video`. The approver only reads it. An approval for a video nobody
               was shown is therefore not constructible, rather than merely disallowed.
  plan-diff    both sides come from the database; the test supplies only which side is empty.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.integrations.fakes import FakeBrevo, FakeDrive, FakeFal, FakeUploadPost
from app.models import PlanShadow, Post
from app.stages.plan_diff import build_diff, print_diff
from app.stages.publish import publish, skip_reason

WEEK = date(2026, 8, 3)


def _publish(session):
    return publish(session, FakeDrive(), FakeFal(), FakeUploadPost(), FakeBrevo(),
                   all_rendered=True, log=lambda *a: None)


# --- 1.1 EMAIL --------------------------------------------------------------------------

@pytest.mark.parametrize("status,review", [
    ("RENDERED", None),                                   # gate-approved + rendered only
    ("RENDERED", {"decision": "approve"}),                # review recorded, status not set
    ("APPROVED_TO_SEND", None),                           # status set, no recorded decision
    ("APPROVED_TO_SEND", {"decision": "reject"}),
    ("APPROVED_TO_SEND", {"decision": "edit"}),
])
def test_email_never_publishes_without_BOTH_status_and_a_recorded_approval(
        session, status, review):
    """THE ONLY IRREVERSIBLE SURFACE. Status alone is not the gate: a status is reachable by
    routes the review never took; a recorded decision is not."""
    session.add(Post(post_id="post_1499", channel="email", week_start=WEEK, status=status,
                     slot="morning", email_review=review, caption="{}"))
    session.flush()
    assert skip_reason(session, session.get(Post, "post_1499")) is not None
    assert _publish(session)["published"] == 0


def test_the_email_refusal_is_reached_through_publish_not_only_by_calling_skip_reason(
        session):
    """A guard nothing calls is not a guard. This asserts the WIRING — publish() itself
    refuses — rather than that the predicate would refuse if asked."""
    session.add(Post(post_id="post_e", channel="email", week_start=WEEK, status="RENDERED",
                     slot="morning", caption="{}"))
    session.flush()
    result = _publish(session)
    assert result["published"] == 0 and result["skipped"] == 1


# --- 1.2 VIDEO --------------------------------------------------------------------------

@pytest.mark.parametrize("status,review", [
    ("RENDERED", None),
    ("RENDERED", {"decision": "approve", "telegram_message_id": 42}),
    ("APPROVED_TO_SEND", None),
    ("APPROVED_TO_SEND", {"decision": "approve"}),         # approved, never DELIVERED
    ("APPROVED_TO_SEND", {"telegram_message_id": 42}),     # delivered, never decided
    ("APPROVED_TO_SEND", {"decision": "reject", "telegram_message_id": 42}),
])
def test_video_never_publishes_without_status_receipt_and_approval(
        session, status, review):
    session.add(Post(post_id="post_v", channel="tiktok", week_start=WEEK, status=status,
                     slot="evening", video_review=review,
                     media_drive_file_id="drive_v.mp4"))
    session.flush()
    assert skip_reason(session, session.get(Post, "post_v")) is not None
    assert _publish(session)["published"] == 0


def test_the_delivery_receipt_is_written_by_the_delivery_code_not_the_approver(repo_root):
    """An approval must not be constructible for a video nobody was shown.

    `telegram_message_id` is read from Telegram's OWN response inside `deliver_video`
    (`body["result"]["message_id"]`). `review_video` only reads it. If the approver could
    write it, the receipt would be supplied by the same side that consumes it — the exact
    circularity the transcription guard exists to prevent, on a different surface.
    """
    src = (repo_root / "plugins" / "artec" / "tools_v4.py").read_text(encoding="utf-8")
    assert 'body["result"]["message_id"]' in src, (
        "the receipt is no longer read from Telegram's response")
    approver = src.split("def _review_video_impl", 1)[-1].split("\ndef ", 1)[0]
    assert "telegram_message_id" in approver, "the approver no longer checks the receipt"
    assert 'review["telegram_message_id"] =' not in approver, (
        "the approver ASSIGNS the receipt — an approval could then be constructed for a "
        "video nobody was shown")


# --- 1.3 PLAN-DIFF ----------------------------------------------------------------------

def test_an_empty_agent_side_is_reported_as_one_sided_not_as_disagreement(session):
    """A8. Job 3 has never completed in production, so an empty plans_shadow is the LIKELY
    state at Sunday 08:00 — not an edge case."""
    for i in range(3):
        session.add(Post(post_id=f"post_b{i}", channel="instagram", week_start=WEEK,
                         status="DRAFT", slot="lunch", angle="a", hook="h",
                         cta_type="shop", plan_source="bespoke"))
    session.flush()

    diff = build_diff(session, WEEK)
    assert diff["one_sided"] is True
    assert diff["agent_count"] == 0 and diff["bespoke_count"] == 3
    # UNDEFINED, not zero. 0.0 reads as "they disagreed about everything".
    assert all(v is None for v in diff["agreement"].values())

    lines: list[str] = []
    print_diff(diff, log=lambda m: lines.append(str(m)))
    text = "\n".join(lines)
    assert "ONE-SIDED" in text
    assert "AGENT planner produced NOTHING" in text
    assert "Job 3" in text
    assert "disjoint slots" not in text          # the false line it used to print


def test_both_sides_present_still_computes_agreement(session):
    """The loud failure must not fire on a real comparison, or it stops being read."""
    session.add(Post(post_id="post_b", channel="instagram", week_start=WEEK, status="DRAFT",
                     slot="lunch", angle="a", hook="h", cta_type="shop",
                     plan_source="bespoke"))
    session.add(PlanShadow(week_start=WEEK, source="agent", channel="instagram",
                           slot="lunch", angle="a", hook="different", cta_type="shop"))
    session.flush()

    diff = build_diff(session, WEEK)
    assert diff["one_sided"] is False
    assert diff["agreement"]["angle"] == 1.0
    assert diff["agreement"]["hook"] == 0.0      # a REAL zero: compared, and they differed
    lines: list[str] = []
    print_diff(diff, log=lambda m: lines.append(str(m)))
    assert "ONE-SIDED" not in "\n".join(lines)


def test_no_plans_at_all_is_distinct_from_one_sided(session):
    """Three states, never collapsed — nothing planned, one planner silent, both spoke."""
    diff = build_diff(session, WEEK)
    lines: list[str] = []
    print_diff(diff, log=lambda m: lines.append(str(m)))
    assert "no plans on either side" in "\n".join(lines)
