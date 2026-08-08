"""RETIRED was a comment, and the job went on firing for weeks.

From artec-scheduler's own production log, 2026-08-07, two consecutive lines:

    22:30:18  measure reminder: telegram send failed (TelegramError)
    22:30:18  measure 2026-08-07: 5 unmeasured post(s), reminder sent

The send failed inside a try/except and the very next statement announced success
unconditionally. It could never have succeeded: D1 removed TELEGRAM_BOT_TOKEN from artec api
AND artec-scheduler so the brain is structurally the sole Telegram owner — so this was a
daily call to a service holding no credentials for it, reporting delivery of a message
nobody received.

`measure-reminder` had been listed in `jobs.RETIRED` the whole time. The registry says
twelve jobs; this fired as a thirteenth. Naming it retired did nothing, because nothing
compared the name to the behaviour — a warning in a comment is not a guard (DECISIONS 95).

THE GUARD, with both sides named: `app/jobs.py` supplies what SHOULD fire; driving a whole
simulated day through `tick()` supplies what DOES. Neither is derived from the other, and a
thirteenth firing of any kind — not just this one — fails the comparison.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.jobs import RETIRED, firings
from app.scheduler import SGT, tick


@pytest.fixture
def spy(session, monkeypatch):
    """Record every job body the tick loop reaches, without running any of them.

    `tick` opens its own transaction via `record_run` (imported inside the function), so the
    fixture session has to be handed in the way the existing scheduler tests do — otherwise
    the loop runs against the global engine and reads a database with no tables.
    """
    import contextlib

    import app.scheduler as sched

    calls: list[str] = []
    monkeypatch.setattr(sched, "run_registry_job",
                        lambda session, job, log=print: calls.append(f"job{job.number}"))
    monkeypatch.setattr(sched, "run_publish_job",
                        lambda session, slot, log=print: calls.append(f"publish:{slot}"))

    class _Rec:
        log = staticmethod(lambda *_a, **_k: None)

    @contextlib.contextmanager
    def fake_record_run(*_a, **_k):
        yield (session, _Rec())

    monkeypatch.setattr("app.db.record_run", fake_record_run)
    return calls


DAY = "2026-08-05"          # a Wednesday


def _drive_a_whole_day(day: str = DAY) -> set[str]:
    """Every one of the 1440 minutes, returning the loop's OWN bookkeeping.

    ASSERTING ON `fired` RATHER THAN ON SPIED CALLS, and the difference is the whole test.
    The first version of this file spied on `run_registry_job` and `run_publish_job` — and
    would NOT have caught the defect it is named for, because the measure reminder fired
    through a third function nobody had thought to spy on. A guard that only sees the
    firings you remembered to enumerate cannot find the one you forgot.

    `fired` is different: the loop adds a key for EVERY firing, because that is how it stops
    itself re-firing within a day. Anything that fires must appear here or it would fire
    again every minute. So the loop cannot fire something without telling this test.
    """
    start = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=SGT)
    fired: set[str] = set()
    for minute in range(1440):
        fired = tick(start + timedelta(minutes=minute), fired, log=lambda *_: None)
    return fired


def _expected_keys(session, day: str = DAY) -> set[str]:
    """What the REGISTRY says should fire on this day. Built from app/jobs.py and
    `slot_times`, never from the scheduler — the two sides must stay independent."""
    from app.config import get_config

    weekday = (datetime.fromisoformat(f"{day}T00:00:00").weekday() + 1) % 7
    keys = set()
    for job, when in firings():
        parts = when.split()
        if len(parts) == 2 and int(parts[0]) != weekday:
            continue
        keys.add(f"{day}|job{job.number}|{when}")
    keys |= {f"{day}|publish|{slot}" for slot in get_config(session, "slot_times")}
    return keys


def test_no_retired_job_fires_during_a_whole_day(session, spy):
    """The regression, by name. The measure reminder wrote `<day>|measure` into `fired`
    every day at 06:30 while sitting in jobs.RETIRED."""
    fired = _drive_a_whole_day()
    assert f"{DAY}|measure" not in fired, "the RETIRED measure reminder still fires"
    for retired in RETIRED:
        stem = retired.split("-")[0]
        assert not any(stem in key.split("|", 1)[1] for key in fired), (
            f"{retired!r} is in jobs.RETIRED and fired anyway: {sorted(fired)}")


def test_nothing_fires_that_the_registry_does_not_list(session, spy):
    """A THIRTEENTH FIRING OF ANY KIND, including one nobody has thought of yet — the same
    shape as the brain entrypoint's exact-set cron check, which exists because every
    "is this job present?" check in the world passes on a superset."""
    unexpected = sorted(_drive_a_whole_day() - _expected_keys(session))
    assert not unexpected, (
        f"the tick loop fired things the registry does not list: {unexpected}. The schedule "
        "is twelve jobs plus the slot-driven publish; anything else is a firing that exists "
        "only in the loop.")


def test_every_registry_firing_actually_happens(session, spy):
    """The other direction, which must be asserted too: a test that only forbids extras
    passes happily on a tick loop that fires NOTHING. Six jobs once had times that nothing
    acted on — a schedule living only in a data structure."""
    missing = sorted(_expected_keys(session) - _drive_a_whole_day())
    assert not missing, f"in the registry and never fired: {missing}"


def test_the_measure_reminder_body_is_gone_not_merely_unscheduled():
    """Leaving the function behind would leave code whose only possible behaviour is to fail
    and claim otherwise — one import away from returning."""
    import app.scheduler as sched

    assert not hasattr(sched, "run_measure_job")


def test_no_route_survives_that_could_only_fail():
    """`/commands/measure-reminder` set its own expiry in writing — "kept invocable only
    until D1 removes TELEGRAM_BOT_TOKEN from this service" — and D1 is done on both
    services. A route that can only raise is not a fallback."""
    from app.jobs import NON_JOB_ROUTES
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/commands/measure-reminder" not in paths
    assert "/commands/measure-reminder" not in NON_JOB_ROUTES


def test_a_config_key_that_schedules_nothing_is_gone():
    """`measure_reminder_time` survived as a required key for artec-scheduler after the only
    thing reading it was retired — so a service would have refused to boot over a setting
    that set nothing."""
    from app.config import OPERATOR_CONSTANTS, REQUIRED_CONFIG_KEYS

    assert "measure_reminder_time" not in OPERATOR_CONSTANTS
    for service, keys in REQUIRED_CONFIG_KEYS.items():
        assert "measure_reminder_time" not in keys, f"still required by {service}"
