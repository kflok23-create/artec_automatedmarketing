"""Recreate v_brief — the PARKED fix has been in the source and NOT in production.

`v_brief` was created by 0001_initial and by nothing since. The view's SQL was later edited
to fix a real defect: the post section was a pure recency window (`ORDER BY week_start DESC
LIMIT 14`), so a post parked weeks ago fell out of it entirely and `read_brief` under-reported
the parked backlog while `read_parked_posts` returned it. post_1485 is exactly that post.

**Alembic never re-runs 0001.** So the edit to `V_BRIEF_SQL` changed the source, changed
what the tests build, and never touched the running database. Production has been serving
the original view this whole time, and the agent's memory note — recorded as an "artec
quirk" and treated as possibly stale — is CORRECT.

Why no test caught it: the test fixture executes the current `V_BRIEF_SQL` string directly,
so tests have always seen the fixed view. Production's schema comes from migrations. This is
the same divergence that produced the `post_id_seq` failures — the fixture and production
disagreeing about who builds the schema — arriving this time through a VIEW, which
`Base.metadata` does not describe at all and `create_all` therefore cannot notice.

CONSEQUENCE, and why this is S2 rather than cosmetic: IDEATE plans against `read_brief`.
An under-reported parked backlog means the planner does not see posts waiting on assets, so
it plans new work while parked work stays invisible — and the wishlist that would service it
is never surfaced. The digest's PARKED section reads from a different query and was correct
throughout, which is precisely why nobody noticed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05
"""
from __future__ import annotations

from alembic import op

from app.models import DROP_V_BRIEF_SQL, V_BRIEF_SQL

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DROP then CREATE, not CREATE OR REPLACE: Postgres refuses to replace a view whose
    # column list changed, and this one's did. A silent failure here would leave the old
    # view in place — the exact outcome this migration exists to end.
    op.execute(DROP_V_BRIEF_SQL)
    op.execute(V_BRIEF_SQL)
    print("v_brief recreated from the current V_BRIEF_SQL "
          "(PARKED posts are now included regardless of age)")


def downgrade() -> None:
    # There is no meaningful downgrade: the previous definition is the DEFECT. Recreating
    # from the current source is the only honest direction, and restoring the recency-only
    # window would deliberately reintroduce an under-reported backlog.
    op.execute(DROP_V_BRIEF_SQL)
    op.execute(V_BRIEF_SQL)
