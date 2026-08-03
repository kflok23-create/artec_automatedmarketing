"""v4 §6 · C — digest preparation.

Digest completeness is safety-critical: after v4 the digest is the ONLY place failures
surface, so a post that changes state and appears nowhere is invisible permanently.
`test_every_state_change_in_24h_appears` is the assertion that guards it.

Also builds the seeded busy-Thursday used for the dry-run deliverable.
"""

import json
from datetime import UTC, date, datetime, timedelta

from app.config import set_config
from app.models import Digest, Order, Post, Run
from app.stages.digest import (
    TELEGRAM_MESSAGE_LIMIT,
    assert_complete,
    build_payload,
    format_money_minor,
    format_usd_cents,
    prepare_digest,
    render_digest_text,
    split_for_telegram,
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


# ---- money is a human surface: minor units are stored, currency is rendered -------------

def test_money_formatter_handles_both_currencies():
    assert format_money_minor("MYR", 21200) == "RM212.00"
    assert format_money_minor("SGD", 7400) == "S$74.00"
    assert format_money_minor("SGD", 0) == "S$0.00"
    assert format_money_minor("MYR", 5) == "RM0.05"
    assert format_money_minor("SGD", 123456789) == "S$1,234,567.89"
    assert format_money_minor("SGD", -7400) == "-S$74.00"
    # an unknown currency renders its code rather than guessing a symbol
    assert format_money_minor("EUR", 100) == "EUR 1.00"


def test_usd_cents_formatter_never_renders_a_real_cost_as_free():
    assert format_usd_cents(250) == "US$2.50"
    assert format_usd_cents(1500) == "US$15.00"
    assert format_usd_cents(18.66) == "US$0.19"
    assert format_usd_cents(0.3) == "<US$0.01"
    assert format_usd_cents(0) == "US$0.00"


def test_numbers_section_renders_currency_not_minor_units(session):
    session.add(Post(post_id="post_8810", week_start=TARGET, channel="instagram",
                     status="PUBLISHED", slot="evening",
                     posted_at=datetime(2026, 8, 27, 9, tzinfo=UTC)))
    session.add(Order(source="billplz", external_id="bp_1", post_id="post_8810",
                      amount_minor=44900, currency="MYR",
                      occurred_at=datetime(2026, 8, 27, 10, tzinfo=UTC)))
    session.flush()
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "net CM RM212.00" in text
    assert "minor" not in text, "minor units are a storage invariant, not an operator surface"


# ---- spend is compared against the right denominator ------------------------------------

def test_run_cap_is_compared_against_one_run_not_the_week(session):
    for day, micros in ((25, 62_208), (26, 62_208), (27, 62_208)):
        session.add(Run(command="render", started_at=datetime.now(UTC) - timedelta(days=27 - day),
                        status="ok", cost_micros=micros))
    session.flush()
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    health = payload["spend_health"]
    assert health["fal_render_runs_this_week"] == 3
    assert health["fal_last_run_cents"] == 6.22          # ONE run, against the per-run cap
    assert health["fal_spend_cents_wtd"] == 18.66        # the week, reported separately
    text = render_digest_text(payload)
    assert "last render run" in text and "run cap US$2.50" in text
    assert "week to date: US$0.19 across 3 render runs" in text


def test_a_single_run_week_says_so_rather_than_leaving_it_to_be_assumed(session):
    session.add(Run(command="render", started_at=datetime.now(UTC), status="ok",
                    cost_micros=62_208))
    session.flush()
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "only one render run this week" in text


def test_agent_cap_is_weekly_on_both_sides(session):
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "agent · week to date:" in text and "weekly cap US$15.00" in text


# ---- an absent warning reads as "no problem" --------------------------------------------

def test_price_table_staleness_is_flagged_every_night(session):
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["spend_health"]["price_status"]["state"] == "never_reconciled"
    assert "never reconciled against fal" in render_digest_text(payload)


def test_a_stale_price_table_is_shouted_not_whispered(session):
    from app.models import EndpointPrice

    for row in session.query(EndpointPrice).all():
        row.as_of = datetime.now(UTC) - timedelta(days=90)
    session.flush()
    text = render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET))
    assert "price table STALE" in text and "clarity-upscaler" in text


# ---- transport: Telegram's 4096-character limit the payload knows nothing about ----------

def _oversized_payload(session):
    """Five unmeasured posts and four parked ones clears the limit, as flagged."""
    for i in range(14):
        session.add(Post(post_id=f"post_87{i:02d}", week_start=TARGET, channel="instagram",
                         status="PARKED", slot="evening",
                         park_reason="waiting on an asset " + "x" * 60,
                         asset_wishlist=[{"target_folder": f"raw-photo/subject-{i}",
                                          "medium": "photo",
                                          "description": "a very long description " * 12}]))
    for i in range(9):
        session.add(Post(post_id=f"post_88{i:02d}", week_start=TARGET, channel="tiktok",
                         status="PUBLISHED", slot="evening",
                         posted_at=datetime(2026, 8, 27, 9, tzinfo=UTC)))
        _pending_email(session, f"post_89{i:02d}")
    session.flush()
    return build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)


def test_an_oversized_digest_splits_on_section_boundaries(session):
    text = render_digest_text(_oversized_payload(session))
    assert len(text) > TELEGRAM_MESSAGE_LIMIT, "fixture must actually clear the limit"
    parts = split_for_telegram(text)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= TELEGRAM_MESSAGE_LIMIT, "a truncated digest is invisible forever"


def test_needs_you_is_always_in_the_first_message(session):
    parts = split_for_telegram(render_digest_text(_oversized_payload(session)))
    assert "1 · NEEDS YOU" in parts[0]


def test_the_split_loses_no_content(session):
    text = render_digest_text(_oversized_payload(session))
    parts = split_for_telegram(text)
    # strip the "(2/5)" continuation headers and the "(continued)" section repeats
    rebuilt = "\n".join(
        ln for i, part in enumerate(parts) for j, ln in enumerate(part.split("\n"))
        if not (i and j == 0) and not ln.endswith("(continued)"))
    assert rebuilt == text


def test_a_short_digest_is_one_message(session):
    parts = split_for_telegram(
        render_digest_text(build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)))
    assert len(parts) == 1


def test_prepared_payload_carries_the_messages_to_send(session):
    payload = prepare_digest(session, brevo=FakeBrevoCount(1), target=TARGET,
                             log=lambda *_: None)
    assert payload["messages"] == [render_digest_text(payload)]


def test_fulfilled_wishlist_entries_drop_off_the_asset_list(session):
    session.add(Post(post_id="post_8971", week_start=TARGET, channel="tiktok",
                     status="PARKED", slot="evening",
                     asset_wishlist=[{"target_folder": "raw-video/assembled",
                                      "medium": "video", "description": "d",
                                      "fulfilled_by": "asset_1"}]))
    session.flush()
    payload = build_payload(session, brevo=FakeBrevoCount(1), target=TARGET)
    assert payload["asset_drop"] == []
