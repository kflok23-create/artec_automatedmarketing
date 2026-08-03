"""v4: endpoint_prices (unit-aware, moved out of config), digests, the two review-gate
columns on posts, and the metrics transcription audit trail.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models import JSONVariant

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    # Fresh installs get these from 0001's metadata.create_all (the models now include
    # them); existing databases get them here. Inspector guards keep both paths idempotent.
    if not _has_table(bind, "endpoint_prices"):
        op.create_table(
            "endpoint_prices",
            sa.Column("endpoint", sa.Text(), primary_key=True),
            sa.Column("rate_micros", sa.BigInteger(), nullable=False),
            sa.Column("unit", sa.Text(), nullable=False),
            sa.Column("source", sa.Text()),
            sa.Column("as_of", sa.DateTime(timezone=True)),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_table(bind, "digests"):
        op.create_table(
            "digests",
            sa.Column("id", BIGINT_PK, primary_key=True, autoincrement=True),
            sa.Column("digest_date", sa.Date(), nullable=False, unique=True),
            sa.Column("payload", JSONVariant),
            sa.Column("delivered_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("email_review", "video_review"):
        if not _has_column(bind, "posts", column):
            op.add_column("posts", sa.Column(column, JSONVariant))
    if not _has_column(bind, "metrics", "operator_message"):
        op.add_column("metrics", sa.Column("operator_message", sa.Text()))

    # Retire the flat price table from config — it lives in endpoint_prices now, and its
    # per-call shape was wrong for the two per-megapixel endpoints. Same for the superseded
    # boolean proof flag and the renamed run cap.
    config = sa.table("config", sa.column("key", sa.Text()))
    bind.execute(config.delete().where(config.c.key.in_(
        ["endpoint_prices_cents", "video_pipeline_proven", "render_budget_cents"])))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "metrics", "operator_message"):
        op.drop_column("metrics", "operator_message")
    for column in ("video_review", "email_review"):
        if _has_column(bind, "posts", column):
            op.drop_column("posts", column)
    if _has_table(bind, "digests"):
        op.drop_table("digests")
    if _has_table(bind, "endpoint_prices"):
        op.drop_table("endpoint_prices")
