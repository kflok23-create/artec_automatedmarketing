"""The three proofs that can only run on the brain, and the reason they could not before.

The all-nine sweep reported these NOT_PROVABLE, each for the same underlying reason:

    prove agent-session: NOT PROVABLE — no message store: HERMES_HOME is not set
    prove sunday-cron:   NOT PROVABLE — hermes is not installed on this service
    prove audit-memory:  NOT PROVABLE — HERMES_HOME is not set on this service

Not defects. The evidence lives on the brain's volume — the hermes message store, the cron
registry, the memory files — and `app/stages/prove.py` runs on artec-scheduler, which has
none of them. A prover pointed at a machine that cannot hold the evidence will report
not_provable forever, however correct it is.

SELF-CONTAINED, because the brain image does not carry the app package — the same boundary
that broke `read_digest` when I imported across it. sqlalchemy textual SQL only. The results
are written to `config.proofs` in Postgres, the same row `app/stages/prove.py` writes, so
`proof_status` and the digest see one set of proofs regardless of which machine produced
each one.

WHAT WOULD MAKE EACH REPORT SUCCESS WITHOUT DEMONSTRATING THE THING — from prove.py's
FALSE_PASS register, enforced here as preconditions rather than left as comments:

    agent-session  "a readable store belonging to a DIFFERENT hermes install"
    sunday-cron    "a cron listing from any hermes install, not artec's"
    audit-memory   "a memory directory that is empty, or somebody else's"
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# The three brain cron jobs, by name. A listing that does not contain OURS proves nothing
# about artec, however healthy it looks — that is the false pass for sunday-cron.
EXPECTED_CRON = ("learn-ideate", "weekly-gate", "nightly-digest")
SUNDAY_CRON = ("learn-ideate", "weekly-gate")


def _home() -> Path | None:
    home = os.environ.get("HERMES_HOME", "")
    return Path(home) if home and Path(home).is_dir() else None


def _active_profile(home: Path) -> str:
    try:
        return (home / "active_profile").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------------------
# agent-session
# ---------------------------------------------------------------------------------------

def prove_agent_session(home: Path) -> tuple[bool, str, dict]:
    """The brain holds a Telegram session and its store is ARTEC'S, not another install's.

    The false pass is "a readable store belonging to a DIFFERENT hermes install", so the
    profile is checked before the contents. $HERMES_HOME/state.db is a documented DECOY that
    opens cleanly and answers with a plausible error (VERIFY.md); the real store is
    profile-scoped, and reading the decoy is exactly how this would pass while proving
    nothing.
    """
    profile = _active_profile(home)
    if profile != "artec-brain":
        return False, (f"active profile is {profile!r}, not 'artec-brain' — this store "
                       "belongs to a different install and proves nothing about artec"), {}

    db = home / "profiles" / profile / "state.db"
    if not db.is_file():
        return False, f"no message store at {db}", {}

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        sessions = conn.execute(
            "SELECT id, source, chat_id FROM sessions WHERE source = 'telegram'").fetchall()
        if not sessions:
            return False, "no telegram session exists in the store", {}
        ids = [row[0] for row in sessions]
        placeholders = ",".join("?" * len(ids))
        # A session row with no messages is a session that never carried a conversation.
        turns = conn.execute(
            f"SELECT COUNT(*) FROM messages WHERE session_id IN ({placeholders}) "
            "AND role = 'user'", ids).fetchone()[0]
    finally:
        conn.close()

    if not turns:
        return False, (f"{len(sessions)} telegram session(s) exist but carry no operator "
                       "turns — a session nobody has spoken in is not a working session"), {}
    return True, (f"{len(sessions)} telegram session(s) in the artec-brain profile, "
                  f"{turns} operator turn(s) recorded"), {
        "sessions": len(sessions), "operator_turns": turns, "profile": profile}


# ---------------------------------------------------------------------------------------
# sunday-cron
# ---------------------------------------------------------------------------------------

def prove_sunday_cron() -> tuple[bool, str, dict]:
    """The two Sunday jobs are registered, are OURS, and resolve to +08:00.

    `hermes cron create` has been observed REJECTING an expression while EXITING 0, so
    registration is only ever proven by LISTING. The listing must contain artec's job names
    — the false pass is "a cron listing from any hermes install, not artec's".
    """
    try:
        # encoding IS PART OF THE PROOF. `text=True` decodes with the platform codec, and
        # `hermes cron list` prints em-dashes: on a cp1252 console the reader thread dies
        # with UnicodeDecodeError, stdout comes back empty, and this reports "cron jobs
        # missing" — a FALSE FAILURE naming the exact defect the entrypoint hard-fails on.
        # The brain is UTF-8 so it would have passed there and lied anywhere else; a prover
        # whose verdict depends on the locale it runs in is not a prover.
        out = subprocess.run(["hermes", "cron", "list"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120).stdout or ""
    except FileNotFoundError:
        return False, "hermes is not on PATH — this proof must run on artec-brain", {}
    except subprocess.TimeoutExpired:
        return False, "hermes cron list timed out", {}
    return evaluate_cron_listing(out)


def evaluate_cron_listing(out: str) -> tuple[bool, str, dict]:
    """The verdict, separated from fetching so it can be tested against listings that no
    test machine would produce on demand.

    THE FALSE PASS IS NOT HYPOTHETICAL. Run on a developer laptop that happens to carry a
    different hermes install, this returned:

        found ['hermes-trading-daily-review', 'hermes-trading-weekly-tactical']

    A healthy listing, correctly formatted, resolving fine — and belonging to somebody's
    trading bot. `hermes cron list` answers about whichever install is on PATH, so "does the
    listing have jobs in it" is a question about the machine, not about artec.
    """
    names = set(re.findall(r"^\s*Name:\s*(\S+)", out, re.MULTILINE))
    missing = [j for j in EXPECTED_CRON if j not in names]
    if missing:
        return False, (f"cron jobs missing from the listing: {missing} — found {sorted(names)}. "
                       "`cron create` exits 0 on failure, so absence here IS the failure"), {}

    # An extra job is a defect too: the twelve are a deployment artefact.
    extra = sorted(names - set(EXPECTED_CRON))
    if extra:
        return False, (f"UNEXPECTED cron job(s) present: {extra}. The brain owns exactly "
                       f"{sorted(EXPECTED_CRON)} and a thirteenth is a defect"), {}

    if "+08:00" not in out:
        return False, ("no next-run carries +08:00 — the times are not resolving in "
                       "Asia/Singapore, so the Sunday jobs would fire at the wrong hour"), {}

    # Job 12 is MON–SAT. A Sunday next-run means the expression was taken as daily.
    digest_block = out.split("nightly-digest", 1)[-1][:400]
    if re.search(r"Next run:\s*\S*[Ss]un", digest_block):
        return False, "nightly-digest lists a SUNDAY next run — it must be MON-SAT", {}

    return True, (f"all {len(EXPECTED_CRON)} brain cron jobs registered and only those; "
                  f"Sunday pair {list(SUNDAY_CRON)} present; next runs resolve +08:00"), {
        "registered": sorted(names)}


# ---------------------------------------------------------------------------------------
# audit-memory
# ---------------------------------------------------------------------------------------

def prove_audit_memory(home: Path) -> tuple[bool, str, dict]:
    """The memory audit runs WHERE THE MEMORY IS, and actually reads bytes.

    The false pass is "a memory directory that is empty, or somebody else's" — and this
    system has already lived it: the audit reported `clean — 0 file(s) scanned` for weeks
    while a stale capability claim sat in the store. So a zero-file scan is a FAILURE here,
    not a pass.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from audit_memory_report import scan

    result = scan(home)
    if result.get("nothing_scanned"):
        return False, (f"the audit read ZERO files — {result.get('scan_warning')} "
                       f"searched: {result.get('searched')}"), result
    if not result.get("clean"):
        hits = result.get("hits", [])
        return False, (f"{len(hits)} finding(s) in agent memory: "
                       f"{[h.get('kind') for h in hits[:5]]}"), result
    return True, (f"audit read {result['scanned_files']} file(s) of real agent memory and "
                  f"found nothing; utilisation {result.get('memory_utilisation')}"), result


# ---------------------------------------------------------------------------------------

def _record(results: dict) -> None:
    """Write into the SAME `config.proofs` row app/stages/prove.py writes, so one set of
    proofs exists regardless of which machine produced each one."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("prove_brain: DATABASE_URL unset — results not recorded", file=sys.stderr)
        return
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    from sqlalchemy import bindparam, create_engine, text
    from sqlalchemy.types import JSON as SAJSON

    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT value FROM config WHERE key = 'proofs'")).first()
        proofs = row[0] if row else {}
        if isinstance(proofs, str):
            proofs = json.loads(proofs)
        proofs = dict(proofs or {})
        # MERGE, never replace: the other six proofs are written by the scheduler/api and
        # overwriting the row would silently un-prove them.
        proofs.update(results)
        conn.execute(
            text("INSERT INTO config (key, value, updated_at) VALUES ('proofs', :v, :t) "
                 "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t")
            .bindparams(bindparam("v", type_=SAJSON())),
            {"v": proofs, "t": datetime.now(UTC)})
    print(f"prove_brain: merged {len(results)} proof(s) into config.proofs")


def main() -> int:
    home = _home()
    now = datetime.now(UTC).isoformat()
    results: dict[str, dict] = {}

    print("=== BRAIN-SIDE PROOFS (the three that need this machine) ===")
    if home is None:
        print("HERMES_HOME is not set or not a directory — cannot prove anything here")
        return 0

    for name, fn in (("agent-session", lambda: prove_agent_session(home)),
                     ("sunday-cron", prove_sunday_cron),
                     ("audit-memory", lambda: prove_audit_memory(home))):
        try:
            ok, detail, evidence = fn()
        except Exception as e:                                # noqa: BLE001
            ok, detail, evidence = False, f"{type(e).__name__}: {e}", {}
        results[name] = {"ok": ok, "detail": detail, "at": now, "evidence": evidence}
        print(f"prove {name}: {'PROVEN' if ok else 'FAILED'} — {detail}")

    try:
        _record(results)
    except Exception as e:                                    # noqa: BLE001
        print(f"prove_brain: could not record: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
