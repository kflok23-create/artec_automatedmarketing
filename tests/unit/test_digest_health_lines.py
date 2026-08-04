"""C.5 and C.7 — the two states that were invisible for a week.

C.5 · `confirm_first_publish` was ambiguous to everyone: inferred once from the existence of
published posts, contradicted once, and settled only by reading the row. A gate whose state
nobody can see is a gate nobody can reason about. **An unstated OPEN is exactly as dangerous
as an unstated CLOSED, in opposite directions** — so the digest says it every night either
way, and when closed it says what clears it.

C.7 · Agent memory sat at 82% of its 2200-char cap. At the cap a new durable fact evicts an
old one with NO SIGNAL. Severity is explicit rather than a single threshold, because
"approaching" and "actively losing things" are different operator decisions. Reported, never
auto-pruned: an automatic deleter is a write tool nobody reviewed, operating on the one store
the agent owns, whose failure mode is silent and permanent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.config import set_config
from app.models import Post
from app.stages.digest import build_payload, render_digest_text

TARGET = date(2026, 8, 4)


def _render(session):
    return render_digest_text(build_payload(session, brevo=None, target=TARGET))


# --- C.5 -------------------------------------------------------------------------------

def test_an_open_gate_is_stated(session):
    set_config(session, "confirm_first_publish", False)
    session.flush()
    text = _render(session)
    assert "publish gate: OPEN" in text
    assert "unattended" in text


def test_a_closed_gate_names_what_it_holds_and_what_clears_it(session):
    set_config(session, "confirm_first_publish", True)
    for pid in ("post_a", "post_b"):
        session.add(Post(post_id=pid, channel="instagram", week_start=TARGET,
                         status="RENDERED", slot="lunch"))
    session.flush()
    text = _render(session)
    assert "publish gate: CLOSED" in text
    assert "holding 2 RENDERED post(s)" in text
    assert "artec publish" in text          # what clears it, not just that it is closed


def test_the_gate_state_is_read_from_config_not_inferred_from_published_posts(session):
    """The inference that was made once and was wrong for a week: 'posts published, so the
    gate must be open'. post_1483/1484 published in the v3 era, before the gate was wired
    into job 7 at all."""
    set_config(session, "confirm_first_publish", True)
    session.add(Post(post_id="post_old", channel="instagram", week_start=TARGET,
                     status="PUBLISHED", slot="lunch", external_post_id="ext_old",
                     posted_at=datetime(2026, 8, 2, tzinfo=UTC)))
    session.flush()
    assert "publish gate: CLOSED" in _render(session)


# --- C.7 -------------------------------------------------------------------------------

@pytest.mark.parametrize("utilisation,expected,absent", [
    (0.50, "memory 50% of 2200 chars", "YELLOW"),
    (0.82, "YELLOW", "RED"),                       # the real reading at last probe
    (0.97, "RED", "YELLOW"),
])
def test_memory_utilisation_severity(session, utilisation, expected, absent):
    set_config(session, "memory_audit", {
        "clean": True, "scanned_files": 2, "audited_at": "2026-08-05T00:00:00+00:00",
        "memory_utilisation": utilisation, "memory_cap": 2200})
    session.flush()
    text = _render(session)
    assert expected in text
    assert absent not in text


def test_the_red_line_says_prune_by_hand_and_never_claims_to_have_pruned(session):
    set_config(session, "memory_audit", {
        "clean": True, "scanned_files": 2, "audited_at": "2026-08-05T00:00:00+00:00",
        "memory_utilisation": 0.97, "memory_cap": 2200})
    session.flush()
    text = _render(session)
    assert "Prune by hand" in text
    assert "nothing prunes automatically" in text
