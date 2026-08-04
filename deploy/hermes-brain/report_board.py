"""READ-ONLY production report: the board, agent_runs, and the orphaned digest payload.

Three questions this pass needs answered FROM PRODUCTION, none of which can be answered
from the source tree or from a local run:

  C.2(b) what is on the board? counts by status, and any RENDERED or PARKED post named
  C.2(c) did agent_runs receive a row for the 2026-08-04 21:00 job? (D-11)
  C.10   the payload of the orphaned 2026-08-03 digest — the first this system produced,
         written under the wrong key, never delivered, still unread

WRITES NOTHING. Every statement here is a SELECT. The one thing worse than not having this
report would be a diagnostic that mutates the state it is diagnosing.

Runs on the brain because the brain already has DATABASE_URL and a boot hook; the same
report would be identical from any service. Bounded output — the digest payload is printed
in full because that is the point, but the board is counts plus named rows, not a dump. The
purge script's 74,812 dropped log messages are the reason that distinction is now explicit.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("report_board: DATABASE_URL unset", file=sys.stderr)
        return 0
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        print("=== C.2(b) THE BOARD — posts by status ===")
        rows = conn.execute(text(
            "SELECT status, COUNT(*) FROM posts GROUP BY status ORDER BY status")).all()
        if not rows:
            print("  (no posts at all — no ideate has ever run)")
        for status, count in rows:
            print(f"  {status:<18} {count}")

        print("\n--- every post NOT in a terminal state, named ---")
        named = conn.execute(text(
            "SELECT post_id, channel, status, slot, week_start, external_post_id, "
            "park_reason FROM posts "
            "WHERE status IN ('DRAFT','RENDERED','APPROVED_TO_SEND','PARKED','FAILED') "
            "ORDER BY post_id")).all()
        if not named:
            print("  (none)")
        for pid, channel, status, slot, week, ext, reason in named:
            print(f"  {pid} {channel} [{status}] slot={slot} week={week} "
                  f"external={ext or '-'}")
            if reason:
                print(f"      reason: {str(reason)[:200]}")

        print("\n--- PUBLISHED posts (the never-republish guard reads external_post_id) ---")
        pub = conn.execute(text(
            "SELECT post_id, channel, external_post_id, posted_at FROM posts "
            "WHERE status = 'PUBLISHED' ORDER BY post_id")).all()
        if not pub:
            print("  (none — nothing has ever been published)")
        for pid, channel, ext, at in pub:
            print(f"  {pid} {channel} external={ext} at={at}")

        print("\n=== C.2(c) D-11 — agent_runs around the 2026-08-04 21:00 job ===")
        try:
            runs = conn.execute(text(
                "SELECT id, job, trigger, started_at, finished_at, status "
                "FROM agent_runs ORDER BY id DESC LIMIT 20")).all()
        except Exception as e:                                # noqa: BLE001
            print(f"  agent_runs unreadable: {type(e).__name__}: {e}")
            runs = []
        if not runs:
            print("  NO ROWS AT ALL. The 21:00 job produced no agent_runs row, so the only")
            print("  evidence it ran is the Telegram message the operator saw. A3 exists so")
            print("  this question has an answer and it currently does not.")
        for row in runs:
            print(f"  {row}")

        print("\n=== C.10 THE ORPHANED DIGEST — the first this system ever produced ===")
        digests = conn.execute(text(
            "SELECT digest_date, delivered_at, payload FROM digests "
            "ORDER BY digest_date")).all()
        if not digests:
            print("  (no digests at all)")
        for digest_date, delivered_at, payload in digests:
            print(f"\n--- digest_date={digest_date} delivered_at={delivered_at} ---")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
            # The pre-split Telegram messages ARE what the operator would have read. Print
            # those verbatim rather than the raw payload: the payload is machine shape, the
            # messages are the artefact a human was supposed to receive.
            messages = (payload or {}).get("messages") if isinstance(payload, dict) else None
            if messages:
                for i, message in enumerate(messages, start=1):
                    print(f"  [message {i}/{len(messages)}]")
                    for line in str(message).splitlines():
                        print(f"  | {line}")
            else:
                print("  (no pre-split messages on the payload — raw keys: "
                      f"{sorted(payload) if isinstance(payload, dict) else type(payload)})")
            if isinstance(payload, dict):
                needs = payload.get("needs_you") or {}
                print(f"  needs_you.empty = {needs.get('empty')}")

        print("\n=== C.2(a) v_brief PARKED omission — LIVE CHECK against the real view ===")
        # The definitive answer, from the running database rather than from the source.
        brief_posts = {r[0].split()[0] for r in conn.execute(text(
            "SELECT line FROM v_brief WHERE section = 'post'")).all() if r[0]}
        parked = {r[0] for r in conn.execute(text(
            "SELECT post_id FROM posts WHERE status = 'PARKED'")).all()}
        missing = sorted(parked - brief_posts)
        print(f"  v_brief post lines: {len(brief_posts)}")
        print(f"  PARKED posts:       {len(parked)} {sorted(parked)}")
        if missing:
            print(f"  *** OMITTED FROM v_brief: {missing}")
            print("  *** The defect is LIVE in production. read_brief under-reports the")
            print("  *** parked backlog and IDEATE plans against it. The agent's memory")
            print("  *** note recording this quirk is CORRECT, not stale.")
        else:
            print("  no PARKED post is missing from v_brief")
    return 0


if __name__ == "__main__":
    sys.exit(main())
