"""posts.withdrawn_at / withdrawn_reason — a post removed at source is not a post that failed.

post_1488 (facebook) and post_1489 (linkedin) published on 2026-08-04 at 01:00, BEFORE page
targeting existed. They went to the personal profile of the account that OAuth'd, because
omitting the page parameter returns success from Upload-Post and nothing downstream can tell
the two outcomes apart. The operator has deleted both from the personal timelines by hand.

WHY A FLAG AND NOT A STATUS. They *were* published — that is a true fact about the ledger,
and `external_post_id` is set, which is what makes `publish` and `retry_post` refuse them
(the never-republish guard, unchanged). Withdrawal is an ADDITIONAL fact, not a replacement
for the first. `posts` is the ledger of record; a withdrawal is an event on the ledger, not
an erasure of it, and rewriting history to make the present tidy is how a system starts
lying about what it did.

WHY IT MATTERS TO `learn`. Left alone, those two rows say: facebook and linkedin published
and earned nothing. `learn` would score both channels down for a reason that has nothing to
do with the creative — a false negative manufactured by a targeting defect. That is exactly
the trap `email_min_recipients` exists to prevent for email, arriving on two other channels.
Invariant 2 again: stale is not zero, and here "removed at source" is not zero either.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

WITHDRAWN = {
    "post_1488": "published to the wrong surface before page targeting existed; "
                 "removed at source by the operator",
    "post_1489": "published to the wrong surface before page targeting existed; "
                 "removed at source by the operator",
}


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "posts", "withdrawn_at"):
        op.add_column("posts", sa.Column("withdrawn_at", sa.DateTime(timezone=True)))
    if not _has_column(bind, "posts", "withdrawn_reason"):
        op.add_column("posts", sa.Column("withdrawn_reason", sa.Text()))

    # Stamped here rather than by hand, so the two posts are marked on every environment
    # that runs migrations — including a restore from backup, where a hand-run would be
    # forgotten and the false negative would silently return.
    posts = sa.table("posts",
                     sa.column("post_id", sa.Text()),
                     sa.column("withdrawn_at", sa.DateTime(timezone=True)),
                     sa.column("withdrawn_reason", sa.Text()))
    now = sa.func.now()
    for post_id, reason in WITHDRAWN.items():
        result = bind.execute(
            posts.update()
            .where(posts.c.post_id == post_id)
            .where(posts.c.withdrawn_at.is_(None))
            .values(withdrawn_at=now, withdrawn_reason=reason))
        print(f"withdrawn: {post_id} ({result.rowcount} row)")


def downgrade() -> None:
    op.drop_column("posts", "withdrawn_reason")
    op.drop_column("posts", "withdrawn_at")
