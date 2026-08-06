"""posts.measurement_waived — a third exclusion reason, beside withdrawn_at.

Five posts sit unmeasured. TWO OF THEM CANNOT BE MEASURED AT ALL: post_1488 and post_1489
were deleted at source and carry `withdrawn_at`. `learn` already excluded them; the digest
kept asking the operator for their figures. **Two surfaces disagreeing about one fact**, and
the one the operator reads was the wrong one.

`measurement_waived` covers the other case: a post nobody is obliged to measure. Created
under a previous system, or published somewhere that returns no analytics, or simply not
worth the operator's transcription. Without it, the only ways to clear a stale METRICS ask
are to invent a figure — which the transcription invariant forbids absolutely — or to delete
the row, which `posts` as the ledger of record forbids equally.

A FLAG WITH A REASON, NOT A STATUS, and never set by code. This migration adds the column
and waives NOTHING: which posts are waived is the operator's judgement, and a system that
waived its own measurement obligations would be marking its own homework.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "posts", "measurement_waived"):
        op.add_column("posts", sa.Column("measurement_waived", sa.JSON()))


def downgrade() -> None:
    op.drop_column("posts", "measurement_waived")
