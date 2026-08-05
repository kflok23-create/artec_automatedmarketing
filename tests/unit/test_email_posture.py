"""§C — the EMAIL REVIEW block states who receives it and whether it was ever previewed.

Brevo list 3's single contact is a REAL CUSTOMER, not the operator's own address. So the
first send is a real marketing email to a real person, written by a model, on a path that
has never run — and email is the one surface with no remedy.

REPORTED, NOT GATED. `send_test` is not mandatory and `approve` is not blocked on it: the
operator decides, and this system's discipline is to surface facts rather than auto-handle
them. What this removes is having to REMEMBER to ask.
"""

from __future__ import annotations

import json
from datetime import date

from app.models import Post
from app.stages.digest import build_payload, render_digest_text

WEEK = date(2026, 8, 3)
TARGET = date(2026, 8, 4)

COPY = json.dumps({"subject": "Print one page", "headline": "H", "body_copy": "B",
                   "cta_text": "Get it", "story_block": "S"})


def _email_post(session, review=None):
    session.add(Post(post_id="post_1499", channel="email", week_start=WEEK,
                     status="RENDERED", slot="morning", caption=COPY,
                     email_review=review, media_drive_file_id="hero.jpg",
                     tracked_url="https://artec.my/p/1499"))
    session.flush()
    return render_digest_text(build_payload(session, brevo=None, target=TARGET))


def test_it_says_the_recipients_are_real_subscribers(session):
    text = _email_post(session)
    assert "REAL SUBSCRIBERS" in text
    assert "not test addresses" in text


def test_no_test_send_is_stated_explicitly_not_left_absent(session):
    """An absent line reads as 'nothing to report'. This is the whole failure family:
    an absence that has to be noticed rather than one that announces itself."""
    text = _email_post(session)
    assert "NO TEST SEND HAS BEEN PERFORMED" in text


def test_a_performed_test_send_is_reported_with_its_date(session):
    text = _email_post(session, review={"test_sends": ["2026-08-05T09:30:00+00:00"]})
    assert "test send performed 1x" in text
    assert "2026-08-05T09:30" in text
    assert "NO TEST SEND" not in text


def test_approve_is_NOT_blocked_on_a_test_send(session):
    """Explicitly asserted so a later reader does not 'tighten' this into a gate. The
    operator decides; the system reports. Making send_test mandatory would be auto-handling
    a judgement that is not the system's to make."""
    from app.stages.publish import skip_reason

    session.add(Post(post_id="post_ok", channel="email", week_start=WEEK,
                     status="APPROVED_TO_SEND", slot="morning", caption=COPY,
                     email_review={"decision": "approve"}))   # no test_sends at all
    session.flush()
    assert skip_reason(session, session.get(Post, "post_ok")) is None
