"""The nine classify themselves, without anybody holding a token.

`POST /commands/prove-all` has existed and worked for days, and the matrix stayed stale the
whole time, because running it needed an authenticated call somebody had to remember to
make. A proof that only happens when a human remembers is a proof that reports the state of
somebody's attention, not the state of the system.

So the sweep runs itself, on the artec api, at boot, in a background thread.

WHY HERE AND NOT ON THE SCHEDULER, since I removed exactly this from exactly there: the
scheduler's boot path gates nine jobs, and anything added to it can cost all nine — that
reasoning has not changed and this does not weaken it. The api owns HTTP serving. A boot
thread that hangs leaves a hung thread; uvicorn is already accepting requests, the webhooks
still answer, and nothing is queued behind it. Different blast radius, different answer.

WHAT THIS DELIBERATELY DOES NOT DO:
  * It does not block startup. The thread is started and never joined.
  * It does not run on every deploy. Two independently supplied times decide (below).
  * It does not run `restore` casually. That proof does CREATE DATABASE -> pg_restore ->
    DROP DATABASE against the live instance, which is the right thing monthly and the wrong
    thing on every redeploy.
  * It does not run twice. A Postgres advisory lock means a second replica no-ops, the same
    mechanism the scheduler uses to avoid double-firing a job.

STALENESS IS A COMPARISON, AND BOTH SIDES ARE NAMED (DECISIONS 112 — a hidden clock is an
unnamed side of a comparison). `now` is a parameter, never read from inside; the other side
is the `at` stamp the previous sweep wrote. Neither is derived from the other, and an
absent stamp means "never run", which is due — not fresh.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

# How long a matrix stays trustworthy. Twelve hours means a burst of redeploys sweeps once,
# and a system left alone still re-checks itself twice a day.
SWEEP_STALE_AFTER = timedelta(hours=12)

# `restore` rides job 8 on day_of_month == 1. Thirty days keeps that cadence when the sweep
# is what triggers it, rather than adding a second, faster one nobody asked for.
RESTORE_STALE_AFTER = timedelta(days=30)

LOCK_NAME = "boot-proof-sweep"

log = logging.getLogger(__name__)


def _stamp(proofs: dict, capability: str | None = None) -> datetime | None:
    """The newest `at` in the recorded proofs, or for one capability.

    Tolerates the two shapes config.proofs has carried: a per-capability dict written by
    run_all, and the brain's merge from prove_brain.py. An unparseable stamp is treated as
    ABSENT rather than as now — reading a corrupt value as fresh is how a sweep stops
    running and nothing says so.
    """
    entries = proofs.values() if capability is None else [proofs.get(capability) or {}]
    stamps = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("at") or entry.get("proved_at")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        stamps.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
    return max(stamps) if stamps else None


def sweep_is_due(proofs: dict | None, now: datetime,
                 stale_after: timedelta = SWEEP_STALE_AFTER) -> tuple[bool, str]:
    """Both sides named: `now` from the caller, the stamp from the last recorded sweep."""
    last = _stamp(proofs or {})
    if last is None:
        return True, "no proof has ever been recorded"
    age = now - last
    if age >= stale_after:
        return True, f"last sweep {age.total_seconds() / 3600:.1f}h ago"
    return False, f"last sweep {age.total_seconds() / 3600:.1f}h ago — still fresh"


def restore_is_due(proofs: dict | None, now: datetime,
                   stale_after: timedelta = RESTORE_STALE_AFTER) -> tuple[bool, str]:
    """`restore` alone, on its own much slower clock.

    It is the only proof that mutates the server — CREATE DATABASE, pg_restore, DROP
    DATABASE. Being conservative here is not timidity: a restore proof that runs on every
    redeploy is a way to discover, during an incident, that the thing had been churning
    databases all along.
    """
    last = _stamp(proofs or {}, "restore")
    if last is None:
        return True, "restore has never been proven"
    age = now - last
    if age >= stale_after:
        return True, f"last restore proof {age.days}d ago"
    return False, f"last restore proof {age.days}d ago — inside the {stale_after.days}d window"


def run_sweep(now: datetime | None = None, force: bool = False,
              log_fn=None) -> dict:
    """One pass of all nine, gated. Returns the matrix, or why it did not run.

    Every exception is caught and reported. This is called from a boot thread, and the one
    thing it must never do is take anything else down with it.
    """
    emit = log_fn or (lambda m: log.info("%s", m))
    now = now or datetime.now(UTC)

    from app.config import get_config
    from app.db import session_scope
    from app.locks import job_lock
    from app.settings import get_settings
    from app.stages.prove import run_all

    with session_scope() as session:
        proofs = get_config(session, "proofs", {}) or {}
        due, why = (True, "forced") if force else sweep_is_due(proofs, now)
        if not due:
            emit(f"proof sweep: skipped — {why}")
            return {"ran": False, "reason": why}

        want_restore, restore_why = (True, "forced") if force else restore_is_due(proofs, now)

        # A SECOND REPLICA MUST NO-OP, not sweep in parallel. `prove_stripe_attribution`
        # refuses outright when a probe order already exists, so two concurrent sweeps do
        # not corrupt anything — they make one of them report a false FAILURE, which is
        # worse than useless in a matrix somebody is about to trust.
        conn = session.connection()
        with job_lock(conn, LOCK_NAME) as owned:
            if not owned:
                emit("proof sweep: another replica holds the lock — no-op")
                return {"ran": False, "reason": "another replica is sweeping"}

            emit(f"proof sweep: running — {why}; restore {'INCLUDED' if want_restore else 'skipped'}"
                 f" ({restore_why})")
            return {"ran": True,
                    "matrix": run_all(session, settings=get_settings(), log=emit,
                                      include_restore=want_restore)}


def start_boot_sweep(force: bool = False) -> threading.Thread:
    """Start the sweep and DO NOT WAIT FOR IT.

    Returns the thread so a test can join it deliberately. Nothing in production ever does.
    """
    def _target() -> None:
        try:
            run_sweep(force=force)
        except Exception as e:                                   # noqa: BLE001
            # Reported, never raised. A proof harness that can break the service it proves
            # is a worse defect than any capability it might have classified.
            log.warning("proof sweep failed (service unaffected): %s: %s", type(e).__name__, e)

    thread = threading.Thread(target=_target, name="boot-proof-sweep", daemon=True)
    thread.start()
    return thread
