"""THE ONE PLACE `digest_date` IS COMPUTED.

The first unattended nightly chain ran on 2026-08-04 and the digest was never delivered.
Both sides were working correctly and they disagreed:

    job 11 (write)   datetime.now(UTC).date() - timedelta(days=1)   → 2026-08-03
    job 12 (read)    date.today()                                    → 2026-08-04

Two defects in one line each. The write side subtracted a day because the payload covers
YESTERDAY's numbers, so the row was keyed by its data window rather than by when it is
delivered. The read side used the server's local date, which is UTC in the container, not
Asia/Singapore. Neither is unreasonable on its own; nothing forced them to agree.

CONVENTION, settled: **`digest_date` is the DELIVERY date, in Asia/Singapore.** The payload
still covers the previous day's numbers — the data window and the key are different things
and conflating them is what broke this. Keying by the data window instead would orphan
Sunday, because job 12 does not run on Sunday, and shift the whole chain by a day.

Both sides now call this function. Neither computes a date independently, and a test asserts
that for the same instant the write key equals the read key — the guard the absence of which
let two correct implementations disagree in production for a whole night.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")


def digest_date_for(now: datetime | None = None) -> date:
    """The delivery date of the digest for this instant, in Asia/Singapore.

    The chain runs 20:30 → 20:40 → 20:55 → 21:00 SGT, so preparation and delivery always
    fall on the same SGT calendar day. Using SGT rather than UTC is what makes that true:
    21:00 SGT is 13:00 UTC the same day, but 00:30 SGT would be 16:30 UTC the day BEFORE,
    and a chain that ever slips past midnight must not silently change its key.
    """
    moment = now.astimezone(SGT) if now is not None else datetime.now(SGT)
    return moment.date()
