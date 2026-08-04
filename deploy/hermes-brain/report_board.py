"""READ-ONLY production report: the board, agent_runs, and the orphaned digest payload.

Three questions this pass needs answered FROM PRODUCTION, none of which can be answered from
the source tree or from a local run:

  C.2(a) is the v_brief PARKED omission still live? — asked of the RUNNING view
  C.2(b) what is on the board? counts by status, every non-terminal post named
  C.2(c) did agent_runs receive a row for the 2026-08-04 21:00 job? (D-11)
  C.10   the payload of the orphaned 2026-08-03 digest — the first this system produced,
         written under the wrong key, never delivered, still unread

WRITES NOTHING. Every statement is a SELECT. A diagnostic that mutates the state it is
diagnosing would be worse than no diagnostic.

TWO RULES LEARNED FROM THIS SCRIPT'S OWN FIRST RUN, both of which it broke:

  1. EACH SECTION IN ITS OWN CONNECTION. The first version ran everything inside one
     `engine.begin()`. The agent_runs query referenced a `trigger` column I had ASSUMED
     rather than checked; Postgres aborted the transaction, and every later statement died
     with InFailedSqlTransaction. The digest payload — the whole point — never ran.

  2. AN ERROR IS NEVER REPORTED AS AN ABSENCE. That failure printed "NO ROWS AT ALL. The
     21:00 job produced no agent_runs row." A finding, asserted on an error path. It is the
     identical conflation this build has been chasing since job 12 announced that job 11 had
     not run, about a job that had run on time. An error and an absence are different facts,
     and merging them manufactures false conclusions from broken queries.

Output is bounded: the digest payload prints in full because that is the point; the board is
counts plus named rows. The memory-purge script's 74,812 dropped log messages are why that
distinction is now explicit rather than assumed.
"""

from __future__ import annotations

import json
import os
import sys


def _normalise(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def main() -> int:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        print("report_board: DATABASE_URL unset", file=sys.stderr)
        return 0

    from sqlalchemy import create_engine, text

    engine = create_engine(_normalise(raw), pool_pre_ping=True)

    def section(title, fn):
        print(f"\n=== {title} ===")
        try:
            with engine.connect() as conn:
                fn(conn, text)
        except Exception as e:                               # noqa: BLE001
            print("  QUERY FAILED. This is an ERROR, not an absence — nothing may be")
            print(f"  concluded from it about the data: {type(e).__name__}: {e}")

    section("C.2(b) THE BOARD — posts by status", _board)
    section("C.2(c) D-11 — agent_runs (did the 21:00 job record a row?)", _agent_runs)
    section("C.10 THE ORPHANED DIGEST — the first this system ever produced", _digests)
    section("C.2(a) v_brief PARKED omission — LIVE, against the running view", _v_brief)
    section("1.1 endpoint_prices — the RED that blocks Sunday's render", _prices)
    section("3.1 confirm_first_publish — THE ROW, not an inference", _gate)
    section("3.2 facebook DRAFTs available for the controlled publish", _fb_drafts)
    return 0


def _prices(conn, text):
    """The RED said `unpriced: ['fal-ai/clarity-upscaler', 'fal-ai/qwen-image-2512/lora']`.
    Three possibilities and they need different fixes: rows absent, rows present but
    unreadable, or rows present with a shape doctor rejects. Print the table and let the
    answer be read rather than guessed."""
    rows = conn.execute(text(
        "SELECT endpoint, rate_micros, unit, source, as_of, active "
        "FROM endpoint_prices ORDER BY endpoint")).all()
    if not rows:
        print("  TABLE IS EMPTY — the seed never reached production. Same class as v_brief")
        print("  and post_id_seq: data that exists in the source and never arrived.")
        return
    for endpoint, micros, unit, source, as_of, active in rows:
        print(f"  {endpoint:<38} {micros:>7} micros  {unit:<16} "
              f"source={source} active={active} as_of={as_of}")
    print(f"  {len(rows)} row(s). The RED is resolved if the two named endpoints appear")
    print("  above; seed_prices runs inside seed_config, which now runs at boot.")


def _gate(conn, text):
    """READ THE ROW. Last pass inferred the gate was cleared from the existence of published
    posts — but post_1483/1484 published in the v3 era and may predate the gate being wired
    into job 7. An inference from a side effect is not a reading."""
    row = conn.execute(text(
        "SELECT key, value, updated_at, set_by FROM config "
        "WHERE key = 'confirm_first_publish'")).first()
    if row is None:
        print("  NO ROW. get_config falls back to its default of True, so the gate is ON")
        print("  and job 7 skips every slot — but nothing in the database says so.")
        return
    print(f"  {row}")
    print("  value True  => gate CLOSED, job 7 skips every slot, nothing publishes")
    print("  value False => gate OPEN, job 7 publishes due posts unattended")


def _fb_drafts(conn, text):
    rows = conn.execute(text(
        "SELECT post_id, channel, slot, week_start, status, media_drive_file_id, "
        "asset_wishlist FROM posts WHERE status = 'DRAFT' ORDER BY post_id")).all()
    if not rows:
        print("  (no DRAFTs)")
        return
    for pid, channel, slot, week, status, media, wishlist in rows:
        marker = "  <== FACEBOOK" if channel == "facebook" else ""
        print(f"  {pid} {channel:<10} slot={slot:<8} week={week} media={media or '-'}{marker}")
        if channel == "facebook" and wishlist:
            print(f"      wishlist: {str(wishlist)[:160]}")


def _board(conn, text):
    rows = conn.execute(text(
        "SELECT status, COUNT(*) FROM posts GROUP BY status ORDER BY status")).all()
    if not rows:
        print("  NO POSTS AT ALL — no ideate has ever run against this database")
    for status, count in rows:
        print(f"  {status:<18} {count}")

    print("\n  --- every post NOT in a terminal state, named ---")
    named = conn.execute(text(
        "SELECT post_id, channel, status, slot, week_start, external_post_id, park_reason "
        "FROM posts WHERE status IN "
        "('DRAFT','RENDERED','APPROVED_TO_SEND','PARKED','FAILED') ORDER BY post_id")).all()
    if not named:
        print("  (none)")
    for pid, channel, status, slot, week, ext, reason in named:
        print(f"  {pid} {channel} [{status}] slot={slot} week={week} external={ext or '-'}")
        if reason:
            print(f"      reason: {str(reason)[:200]}")

    print("\n  --- PUBLISHED (the never-republish guard reads external_post_id) ---")
    pub = conn.execute(text(
        "SELECT post_id, channel, external_post_id, posted_at FROM posts "
        "WHERE status = 'PUBLISHED' ORDER BY post_id")).all()
    if not pub:
        print("  (none — nothing has ever been published)")
    for pid, channel, ext, at in pub:
        print(f"  {pid} {channel} external={ext} at={at}")


def _agent_runs(conn, text):
    # COLUMNS READ FROM information_schema, not assumed. Assuming `trigger` existed is what
    # aborted the first run's transaction — and `trigger` does NOT exist, which is itself
    # the answer to whether C.8 can record a manual trigger today. It cannot; that needs a
    # migration.
    cols = [r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'agent_runs' ORDER BY ordinal_position")).all()]
    print(f"  agent_runs columns: {cols}")
    if "trigger" not in cols:
        print("  NOTE: there is no `trigger` column. C.8 requires agent_runs.trigger = "
              "'manual' to distinguish a run-now mirror from a cron firing; that needs a "
              "migration and does not exist yet.")
    if not cols:
        print("  the agent_runs TABLE does not exist")
        return
    total = conn.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar()
    print(f"  total rows: {total}")
    if not total:
        print("  ZERO ROWS. Every brain job so far — including the 2026-08-04 21:00 "
              "digest-delivery — recorded nothing. The only evidence that job ran is the "
              "Telegram message the operator saw. A3 exists so this question has an answer, "
              "and today it does not.")
        return
    selectable = ", ".join(c for c in
                          ("id", "job", "session_id", "started_at", "finished_at",
                           "status", "tokens", "cost_cents") if c in cols)
    for row in conn.execute(text(
            f"SELECT {selectable} FROM agent_runs ORDER BY id DESC LIMIT 20")).all():
        print(f"  {row}")


def _digests(conn, text):
    rows = conn.execute(text(
        "SELECT digest_date, delivered_at, payload FROM digests ORDER BY digest_date")).all()
    if not rows:
        print("  (no digests at all)")
        return
    for digest_date, delivered_at, payload in rows:
        print(f"\n  --- digest_date={digest_date} delivered_at={delivered_at} ---")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                pass
        # The pre-split Telegram messages ARE what the operator would have received. Print
        # those rather than the raw payload: the payload is machine shape, the messages are
        # the artefact a human was supposed to read.
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if messages:
            for i, message in enumerate(messages, start=1):
                print(f"  [message {i}/{len(messages)}]")
                for line in str(message).splitlines():
                    print(f"  | {line}")
        elif isinstance(payload, dict):
            print(f"  (no pre-split messages; payload keys: {sorted(payload)})")
        else:
            print(f"  (payload is {type(payload).__name__}, not a dict)")
        if isinstance(payload, dict):
            needs = payload.get("needs_you") or {}
            print(f"  needs_you.empty = {needs.get('empty')}")


def _v_brief(conn, text):
    """The definitive answer, from the RUNNING view rather than from the source constant."""
    brief = {r[0].split()[0] for r in conn.execute(text(
        "SELECT line FROM v_brief WHERE section = 'post'")).all() if r[0]}
    parked = {r[0] for r in conn.execute(text(
        "SELECT post_id FROM posts WHERE status = 'PARKED'")).all()}
    print(f"  v_brief post lines: {len(brief)}")
    print(f"  PARKED posts:       {len(parked)} {sorted(parked)}")
    missing = sorted(parked - brief)
    if missing:
        print(f"  *** OMITTED FROM v_brief: {missing}")
        print("  *** The defect is LIVE. read_brief under-reports the parked backlog and")
        print("  *** IDEATE plans against it. The agent's memory note is CORRECT.")
    elif not parked:
        print("  no PARKED posts exist, so this cannot distinguish a fixed view from a")
        print("  broken one — an empty board proves nothing, which is the publish-by-slot")
        print("  lesson. Re-check once a post is parked.")
    else:
        print("  every PARKED post appears in v_brief — migration 0007 took effect")


if __name__ == "__main__":
    sys.exit(main())
