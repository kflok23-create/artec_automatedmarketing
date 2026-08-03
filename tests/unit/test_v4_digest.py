"""v4 §6 · C — digest preparation.

Digest completeness is safety-critical: after v4 the digest is the ONLY place failures
surface, so a post that changes state and appears nowhere is invisible permanently.
`test_every_state_change_in_24h_appears` is the assertion that guards it.

Also builds the seeded busy-Thursday used for the dry-run deliverable.
"""

import json
from datetime import UTC, date, datetime

from app.config import set_config
from app.models import Digest, Order, Post, Run
from app.stages.digest import (
    assert_complete,
    build_payload,
    prepare_digest,
    render_digest_text,
)

TARGET = date(2026, 8, 27)


class FakeBrevoCount:
    def __init__(self, count=1):
        self._count = count

    def get_list_count(self):
        if self._count is None:
            raise RuntimeError("brevo unreachable")
        return self._count


def _now():
    return datetime.now(UTC)


# ---- structure -----------------------------------------------------------------------

def test_empty_day_says_nothing_needs_you(session):
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["needs_you"]["empty"] is True
    text = render_digest_text(payload)
    assert "Nothing needs you tonight" in text


def test_prepare_is_idempotent_on_digest_date(session):
    prepare_digest(session, brevo=FakeBrevoCount(1), target=TARGET, log=lambda *_: None)
    prepare_digest(session, brevo=FakeBrevoCount(1), target=TARGET, log=lambda *_: None)
    rows = session.query(Digest).filter(Digest.digest_date == TARGET).all()
    assert len(rows) == 1                       # replaced, never duplicated
    assert rows[0].delivered_at is None         # re-prepared → re-deliverable


def test_sections_appear_in_reading_order(session):
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    order = [text.index(s) for s in ("1 · NEEDS YOU", "2 · WENT OUT TODAY",
                                     "3 · TONIGHT'S ASSET DROP", "4 · NUMBERS",
                                     "5 · SPEND & HEALTH")]
    assert order == sorted(order)


# ---- the live Brevo count is read, never cached, never zeroed --------------------------

def test_brevo_count_is_read_live_at_preparation(session):
    _pending_email(session)
    payload = build_payload(session, brevo=FakeBrevoCount(37), target=TARGET)
    assert payload["needs_you"]["brevo_list_count"] == 37
    assert payload["spend_health"]["brevo_list_count"] == 37


def test_unreachable_brevo_reports_unavailable_not_zero(session):
    _pending_email(session)
    payload = build_payload(session, brevo=FakeBrevoCount(None), target=TARGET)
    assert payload["needs_you"]["brevo_list_count"] is None
    text = render_digest_text(payload)
    assert "UNAVAILABLE" in text and "not zero" in text


def test_count_below_threshold_is_labelled_not_killed(session):
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert "below measurement threshold" in render_digest_text(payload)


# ---- lane rule + stale ≠ zero -----------------------------------------------------------

def test_revenue_and_engagement_are_separate_blocks(session):
    session.add(Post(post_id="post_8801", week_start=TARGET, channel="instagram",
                     status="PUBLISHED", slot="evening",
                     posted_at=datetime(2026, 8, 27, 9, tzinfo=UTC)))
    session.add(Order(source="stripe", external_id="cs_d1", post_id="post_8801",
                      amount_minor=13900, currency="SGD",
                      occurred_at=datetime(2026, 8, 27, 10, tzinfo=UTC)))
    session.flush()
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    numbers = payload["numbers"]
    assert numbers["revenue"]["by_currency"]["SGD"]["orders"] == 1
    assert "impressions" not in json.dumps(numbers["revenue"])
    assert "net_cm_minor" not in json.dumps(numbers["engagement"])
    assert "post_8801" in numbers["engagement"]["unmeasured_posts"]
    assert "unmeasured (not zero)" in render_digest_text(payload)


def test_cac_is_reported_as_health_never_a_kill_criterion(session):
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["spend_health"]["cac_is_health_only"] is True
    assert "health only, never a kill rule" in render_digest_text(payload)


def test_scouting_status_reported_every_night(session):
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "scouting:" in text


def test_spend_reads_from_runs_and_agent_runs(session):
    from app.models import AgentRun

    session.add(Run(command="render", started_at=_now(), status="ok", cost_micros=62_208))
    session.add(AgentRun(job="learn-ideate", started_at=_now(), status="ok", cost_cents=9))
    session.flush()
    health = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)["spend_health"]
    assert health["fal_spend_cents_wtd"] == 6.22      # 1080x1920 upscale
    assert health["agent_spend_cents_wtd"] == 9


# ---- completeness: safety-critical ------------------------------------------------------

def _pending_email(session, pid="post_8700"):
    session.add(Post(post_id=pid, week_start=TARGET, channel="email", status="RENDERED",
                     slot="morning", media_drive_file_id="gen_hero.jpg",
                     tracked_url="https://artec.my/?code=EMAIL50&utm_campaign=" + pid,
                     caption=json.dumps({
                         "subject": "The 10-minute focus builder",
                         "headline": "Build focus, one block at a time",
                         "body_copy": "Artec blocks snap on every side, so a seven-year-old "
                                      "can go from idea to finished build without an adult "
                                      "untangling it for them.",
                         "cta_text": "Get S$10 off",
                         "story_block": "We made this because focus is built, not born."})))
    session.flush()
    return pid


def test_every_state_change_in_24h_appears(session):
    """A post that changes state and appears nowhere is invisible to the operator
    permanently — this is the assertion that stops that."""
    session.add(Post(post_id="post_8901", week_start=TARGET, channel="instagram",
                     status="PARKED", slot="evening", park_reason="no asset",
                     asset_wishlist=[{"target_folder": "raw-photo/assembled",
                                      "medium": "photo", "description": "assembled crane"}]))
    session.add(Post(post_id="post_8902", week_start=TARGET, channel="tiktok",
                     status="FAILED", slot="evening", park_reason="upload timeout"))
    session.add(Post(post_id="post_8903", week_start=TARGET, channel="facebook",
                     status="PUBLISHED", slot="lunch", external_post_id="up_9",
                     posted_at=datetime(2026, 8, 27, 12, tzinfo=UTC)))
    _pending_email(session, "post_8904")
    session.flush()

    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert assert_complete(session, payload) == []
    blob = json.dumps(payload, default=str)
    for pid in ("post_8901", "post_8902", "post_8903", "post_8904"):
        assert pid in blob, f"{pid} changed state and is invisible in the digest"


def test_completeness_warning_is_surfaced_inside_the_digest(session, monkeypatch):
    session.add(Post(post_id="post_8950", week_start=TARGET, channel="instagram",
                     status="PUBLISHED", slot="evening"))
    session.flush()
    # Simulate a payload that lost a post — the warning must appear in the digest itself,
    # not only in a log line nobody reads.
    monkeypatch.setattr("app.stages.digest.build_payload",
                        lambda *a, **k: {"date": str(TARGET),
                                         "needs_you": {"empty": True, "video_review": [],
                                                       "email_review": [], "unmeasured": [],
                                                       "failures": [], "parked": [],
                                                       "doctor_red": [], "orphaned_slots": [],
                                                       "brevo_list_count": 1},
                                         "went_out_today": [], "asset_drop": [],
                                         "numbers": {"revenue": {"by_currency": {},
                                                                 "unattributed": 0},
                                                     "engagement": {"measured": {},
                                                                    "unmeasured_posts": []}},
                                         "spend_health": {"fal_spend_cents_wtd": 0,
                                                          "render_run_cap_cents": 250,
                                                          "agent_spend_cents_wtd": 0,
                                                          "agent_weekly_cap_minor": 500,
                                                          "system_cac_cents": {},
                                                          "cac_is_health_only": True,
                                                          "brevo_list_count": 1,
                                                          "email_min_recipients": 25,
                                                          "price_table": [],
                                                          "stale_prices": [],
                                                          "scouting": {"available": False,
                                                                       "reason": "x"}}})
    payload = prepare_digest(session, brevo=FakeBrevoCount(1), target=TARGET,
                             log=lambda *_: None)
    assert "post_8950" in payload["completeness_warning"]["posts_missing_from_digest"]
    assert "invisible to the operator" in render_digest_text(payload)


def test_doctor_red_lines_reach_the_digest(session):
    set_config(session, "last_doctor", {
        "at": "2026-08-27T20:40:00+00:00",
        "checks": [{"name": "google drive bank", "status": "RED",
                    "detail": "write probe permission denied"},
                   {"name": "postgres reachable", "status": "GREEN", "detail": "ok"}]})
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert len(payload["needs_you"]["doctor_red"]) == 1
    assert "DOCTOR RED" in render_digest_text(payload)


def test_orphaned_slots_reach_the_digest(session):
    session.add(Post(post_id="post_8960", week_start=TARGET, channel="instagram",
                     status="RENDERED", slot="afternoon"))
    session.flush()
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["needs_you"]["orphaned_slots"][0]["post_id"] == "post_8960"
    assert "ORPHAN SLOT" in render_digest_text(payload)


def test_asset_drop_lists_exact_folders(session):
    session.add(Post(post_id="post_8970", week_start=TARGET, channel="tiktok",
                     status="PARKED", slot="evening",
                     asset_wishlist=[{"target_folder": "raw-video/child-face",
                                      "medium": "video",
                                      "description": "child snapping two blocks together"}]))
    session.flush()
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "raw-video/child-face/" in text


def test_fulfilled_wishlist_entries_drop_off_the_asset_list(session):
    session.add(Post(post_id="post_8971", week_start=TARGET, channel="tiktok",
                     status="PARKED", slot="evening",
                     asset_wishlist=[{"target_folder": "raw-video/assembled",
                                      "medium": "video", "description": "d",
                                      "fulfilled_by": "asset_1"}]))
    session.flush()
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["asset_drop"] == []
