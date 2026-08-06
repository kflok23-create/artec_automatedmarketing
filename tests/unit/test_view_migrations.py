"""A VIEW CHANGED IN SOURCE IS NOT A VIEW CHANGED IN PRODUCTION.

`v_brief` was created by 0001_initial and by nothing since. Its SQL was later edited to fix
the PARKED-omission defect. Alembic never re-runs 0001, so the fix lived in the source and
in every test run for weeks while production served the original view.

NOTHING COULD HAVE CAUGHT IT:
  - the test fixture executes the current `V_BRIEF_SQL` string directly, so tests always
    saw the fixed view
  - `Base.metadata` does not describe views at all, so `create_all` cannot notice
  - no migration referenced the view after 0001, so `alembic upgrade head` was a no-op
  - the digest's PARKED section reads a different query and was correct throughout, which
    is why the symptom never surfaced anywhere a human looked

This test is a HASH PIN. It does not verify production — nothing local can. It makes the
next edit to `V_BRIEF_SQL` fail loudly, with instructions, so the migration is written in
the same change as the SQL rather than discovered months later from an agent's memory note.

WHAT SUPPLIES EACH SIDE: the hash is computed from `app/models.py`'s live constant; the
expected value is a LITERAL recorded here at the moment a migration was written. Editing the
SQL moves one side and not the other. Neither can be satisfied by the other, and the only
way to make it pass is to do the thing it is asking for.

That distinction is not pedantry — the first version of this file got it wrong. It read
`V_BRIEF_SQL_SHA256 = sha256(normalise(V_BRIEF_SQL))`, deriving the expected value from the
thing under test, so the assertion could never fail whatever the SQL became. The standing
review question failing inside the guard written to catch a related defect, in the same
change that named the class. Caught by asking the question of my own work rather than only
of the code being reviewed.
"""

from __future__ import annotations

import hashlib
import re

from app.models import V_BRIEF_SQL

# Bumped ONLY when a migration recreating the view lands in the same commit.
# History:
#   0001_initial          — original definition, recency window only
#   0007_recreate_v_brief — PARKED posts included regardless of age (2026-08-05)
# A LITERAL, NOT A COMPUTATION. The first version of this line read
#     V_BRIEF_SQL_SHA256 = sha256(normalise(V_BRIEF_SQL))
# which derives the expected value FROM THE THING UNDER TEST — both sides from one source,
# so the assertion could never fail no matter what the SQL became. That is the standing
# review question failing inside the guard written to catch a related defect, in the same
# change. The literal below is the only form that can disagree with the source.
V_BRIEF_SQL_SHA256 = "b2551af540efae296539a7c7d9769e571238858996064a0515ce5636e9bd92da"

LATEST_VIEW_MIGRATION = "0007_recreate_v_brief"


def _normalised_hash(sql: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", sql).strip().encode("utf-8")).hexdigest()


def test_a_migration_exists_that_recreates_the_view(repo_root):
    """The fix that never shipped. Without a migration touching the view, an edit to
    V_BRIEF_SQL changes tests and nothing else."""
    path = repo_root / "app" / "migrations" / "versions" / f"{LATEST_VIEW_MIGRATION}.py"
    assert path.is_file(), (
        f"{LATEST_VIEW_MIGRATION}.py is missing — v_brief would be created only by "
        "0001_initial, which alembic never re-runs")
    src = path.read_text(encoding="utf-8")
    assert "DROP_V_BRIEF_SQL" in src and "V_BRIEF_SQL" in src
    assert "op.execute(V_BRIEF_SQL)" in src


def test_the_view_migration_is_at_or_after_the_head_chain(repo_root):
    """A view migration that is not reachable from head never runs either."""
    versions = repo_root / "app" / "migrations" / "versions"
    revisions, downs = set(), set()
    for path in versions.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision = "([^"]+)"', src, re.MULTILINE)
        down = re.search(r'^down_revision = "([^"]+)"', src, re.MULTILINE)
        if rev:
            revisions.add(rev.group(1))
        if down:
            downs.add(down.group(1))
    heads = revisions - downs
    assert heads == {"0010"}, (
        f"expected a single head 0010; found {sorted(heads)}. A view migration that is not "
        "on the path to head is exactly as inert as no migration at all.")


def test_editing_the_view_sql_forces_a_new_migration():
    """THE PIN. If this fails you have changed `V_BRIEF_SQL` — good — and must now:

        1. add app/migrations/versions/000N_recreate_v_brief.py that DROPs and CREATEs it
        2. point its down_revision at the current head
        3. update LATEST_VIEW_MIGRATION above
        4. update the History comment with what changed and why

    Do not simply update the hash. The hash is not the guard; the migration is. Updating the
    hash alone reproduces the original defect exactly: source fixed, production untouched,
    tests green.
    """
    assert _normalised_hash(V_BRIEF_SQL) == V_BRIEF_SQL_SHA256


def test_the_parked_fix_is_actually_in_the_sql():
    """The specific defect, asserted by behaviour of the text rather than by a hash — a hash
    says 'something changed', this says 'the thing that was wrong is right'."""
    flat = re.sub(r"\s+", " ", V_BRIEF_SQL)
    assert "status = 'PARKED'" in flat, (
        "v_brief no longer unions PARKED posts — read_brief will under-report the parked "
        "backlog and IDEATE will plan against it")
    assert "UNION" in flat.upper()


def test_v_brief_is_created_by_a_migration_not_only_by_the_fixture(repo_root):
    """The disconnected-guard question aimed at a schema object: is it reachable from the
    path production actually uses?"""
    versions = repo_root / "app" / "migrations" / "versions"
    creators = [p.name for p in versions.glob("*.py")
                if "op.execute(V_BRIEF_SQL)" in p.read_text(encoding="utf-8")]
    assert len(creators) >= 2, (
        f"only {creators} create v_brief. 0001 alone is not enough — it never re-runs, so "
        "every later edit to the view is invisible to production.")
