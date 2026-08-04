"""agent_runs.trigger — no cron job has ever recorded a run.

`agent_runs` holds two rows and both are operator conversations. Jobs 3, 5 and 12 have never
written one. On 2026-08-09 jobs 3 (07:00 learn+ideate) and 5 (09:00 THE GATE) run for the
first time, and today, if either fails, there is no record that it ran at all — only its
absence, which is indistinguishable from the cron never firing.

That distinction is the whole point. `hermes cron create` exits 0 on failure, which is why
registration is verified by listing; the same reasoning applies to execution. An absent row
must mean "did not run", and it can only mean that if a run that DOES happen always leaves
one.

`trigger` separates a cron firing from a `run-now` mirror. Without it, a manually triggered
job and a scheduled one are indistinguishable in the ledger, and "the Sunday gate ran" would
be true of a run the operator started by hand on a Tuesday.

  cron   — fired by hermes-agent's scheduler at its registered time
  manual — invoked over authenticated HTTPS by the operator (C.8's run-now mirrors)

Existing rows are backfilled to 'manual': both are operator conversations, which is what
manual means, and leaving them NULL would make the column's first real use ambiguous.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    # posts.target_check — the three-state publish-target verification. Added here rather
    # than in its own migration because it ships in the same change as the code that
    # writes it; a column that arrives a deploy later is a column the code cannot use.
    if not _has_column(bind, "posts", "target_check"):
        op.add_column("posts", sa.Column("target_check", sa.JSON()))
    if not _has_column(bind, "agent_runs", "trigger"):
        op.add_column("agent_runs", sa.Column("trigger", sa.Text()))
    runs = sa.table("agent_runs",
                    sa.column("id", sa.BigInteger()),
                    sa.column("trigger", sa.Text()))
    result = bind.execute(
        runs.update().where(runs.c.trigger.is_(None)).values(trigger="manual"))
    print(f"agent_runs.trigger added; {result.rowcount} existing row(s) backfilled to "
          "'manual' (both are operator conversations)")


def downgrade() -> None:
    op.drop_column("agent_runs", "trigger")
