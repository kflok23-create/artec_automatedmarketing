"""v4: runs.cost_micros — a queryable meter for week-to-date fal spend.

The digest's SPEND & HEALTH block reports render spend against the USD 2.50 cap. Parsing
it back out of the `runs.log` text would be exactly the kind of fragile inference this
build keeps eliminating, so the render stage records it as a number.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "runs", "cost_micros"):
        op.add_column("runs", sa.Column("cost_micros", sa.BigInteger()))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "runs", "cost_micros"):
        op.drop_column("runs", "cost_micros")
