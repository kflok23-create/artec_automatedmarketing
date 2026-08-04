"""THE WRITE KEY MUST EQUAL THE READ KEY, FOR THE SAME INSTANT.

The first unattended nightly chain ran on 2026-08-04. All four jobs fired on time. Job 11
wrote a digest carrying ACTION REQUIRED content. Nothing was delivered, and what reached
Telegram said job 11 had not run.

    job 11 (write)   datetime.now(UTC).date() - timedelta(days=1)   → 2026-08-03
    job 12 (read)    date.today()                                    → 2026-08-04

Both implementations were correct in isolation. Nothing compared them. That is the standing
review question exactly: each side supplied its own notion of the key, so the check could
never fail — there was no check.

What supplies each side here: the WRITE key comes from `prepare_digest` actually running and
inserting a row; the READ key comes from `_read_digest_impl` resolving its target. The test
supplies only the instant. Neither side is asserted against a literal date, because a literal
would be a third convention and would pass while the two real ones disagreed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import plugins.artec.tools  # noqa: F401  — tools_v4 is imported THROUGH the package;
from app.digest_dates import SGT, digest_date_for  # importing it first hits a cycle
from app.stages.digest import prepare_digest
from plugins.artec.tools_v4 import _read_digest_impl

# The nightly chain, and the edges that would break a UTC-based key.
INSTANTS = [
    datetime(2026, 8, 4, 12, 55, tzinfo=UTC),    # 20:55 SGT — job 11, the night that broke
    datetime(2026, 8, 4, 13, 0, tzinfo=UTC),     # 21:00 SGT — job 12, same SGT day
    datetime(2026, 8, 4, 16, 30, tzinfo=UTC),    # 00:30 SGT the 5th — UTC says the 4th
    datetime(2026, 8, 4, 0, 30, tzinfo=UTC),     # 08:30 SGT, same day both ways
]


@pytest.mark.parametrize("instant", INSTANTS)
def test_the_key_is_the_sgt_calendar_date_not_utc(instant):
    assert digest_date_for(instant) == instant.astimezone(SGT).date()


def test_prepare_and_read_agree_for_the_same_instant(session, engine, monkeypatch):
    """THE GUARD. Prepare a digest, then read it back through the delivery tool's own date
    resolution. If either side changes convention, this fails."""
    # A Wednesday 20:55 SGT — job 11's slot. Not a Sunday, so job 12 does not refuse.
    instant = datetime(2026, 8, 5, 12, 55, tzinfo=UTC)
    assert instant.astimezone(SGT).weekday() != 6

    prepare_digest(session, brevo=None, target=digest_date_for(instant), log=lambda *a: None)
    session.commit()

    result = _read_digest_impl(now=instant, engine=engine)
    assert result["prepared"] is True, (
        f"job 12 could not find the row job 11 just wrote: {result.get('note')}. "
        "The write key and the read key have diverged again.")
    assert result["date"] == str(digest_date_for(instant))


def test_a_digest_under_a_different_key_is_a_named_fault_not_a_missing_run(
        session, engine):
    """STATE 2, the one that fired on 2026-08-04 and had no words for itself.

    A row exists under another date. The old code said "job 11 has not run", which sent the
    operator to debug the scheduler. It must name both dates and call itself a fault.
    """
    written = datetime(2026, 8, 4, 12, 55, tzinfo=UTC)
    asked_for = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)      # the next day, no row

    prepare_digest(session, brevo=None, target=digest_date_for(written), log=lambda *a: None)
    session.commit()

    result = _read_digest_impl(now=asked_for, engine=engine)
    assert result["deliver"] is False
    assert result["fault"] == "digest_date_mismatch"
    assert result["found_date"] == str(digest_date_for(written))
    assert result["expected_date"] == str(digest_date_for(asked_for))
    # The failure mode being guarded against is the WORDING, so assert the wording:
    # it must not tell the operator the run is missing when the run happened.
    assert result["note"].startswith("FAULT:")
    assert "not a missing run" in result["note"]


def test_no_digest_at_all_still_reports_job_11_did_not_run(session, engine):
    """STATE 3 must survive the fix — an empty table is genuinely a missing run."""
    result = _read_digest_impl(now=datetime(2026, 8, 5, 13, 0, tzinfo=UTC), engine=engine)
    assert result["deliver"] is False
    assert result.get("fault") is None
    assert "job 11 has not run" in result["note"]


def test_neither_side_computes_a_date_of_its_own(repo_root):
    """Structural, from OUTSIDE both files — the shape that catches drift before it ships.

    Grepping for the two idioms that caused this is not elegant, and it is the only check
    that fails when someone reintroduces one. A behavioural test passes as long as the two
    sides happen to agree today.
    """
    for path in ("app/stages/digest.py", "plugins/artec/tools_v4.py"):
        src = (repo_root / path).read_text(encoding="utf-8")
        body = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        assert "date.today()" not in body, (
            f"{path} computes a date of its own with date.today() — that is the container's "
            "UTC date. Use digest_date_for() so both sides cannot diverge.")
        assert "timedelta(days=1)" not in body or "digest_date_for" in body, (
            f"{path} looks like it is keying the digest by its data window again")
