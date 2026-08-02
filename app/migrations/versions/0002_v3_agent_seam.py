"""v3: plans_shadow + agent_runs tables, posts.plan_source / posts.gate_action columns,
and the post_1485 re-park (its generated video will not play; generated video is gone —
do not attempt to repair a generated asset).

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.models import JSONVariant

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    # Fresh installs create these via 0001's metadata.create_all (models now include them);
    # existing databases get them here. Inspector guards keep both paths idempotent.
    if not _has_table(bind, "plans_shadow"):
        op.create_table(
            "plans_shadow",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("channel", sa.Text(), nullable=False),
            sa.Column("angle", sa.Text()),
            sa.Column("hook", sa.Text()),
            sa.Column("cta_type", sa.Text()),
            sa.Column("cta_placement", sa.Text()),
            sa.Column("keywords", JSONVariant),
            sa.Column("slot", sa.Text()),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("week_start", "channel", "slot", "source",
                                name="uq_shadow_week_channel_slot_src"),
        )
    if not _has_table(bind, "agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("job", sa.Text()),
            sa.Column("session_id", sa.Text()),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.Text()),
            sa.Column("tools_called", JSONVariant),
            sa.Column("tokens", sa.BigInteger()),
            sa.Column("cost_cents", sa.BigInteger()),
        )
    if not _has_column(bind, "posts", "plan_source"):
        op.add_column("posts", sa.Column("plan_source", sa.Text()))
    if not _has_column(bind, "posts", "gate_action"):
        op.add_column("posts", sa.Column("gate_action", JSONVariant))

    # Data fix: post_1485's generated video will not play and generative video is removed.
    # PARK it with a wishlist in the bank's folder vocabulary. Idempotent; never touches a
    # published post.
    posts = sa.table(
        "posts",
        sa.column("post_id", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("park_reason", sa.Text()),
        sa.column("asset_wishlist", JSONVariant),
    )
    bind.execute(
        posts.update()
        .where(posts.c.post_id == "post_1485")
        .where(posts.c.status.notin_(["PUBLISHED", "PARKED"]))
        .values(
            status="PARKED",
            park_reason="v3 migration: generated video will not play; generative video is "
                        "removed — re-render from real raw-video/ footage once shot",
            asset_wishlist=[{
                "target_folder": "raw-video/assembled",
                "medium": "video",
                "aspect": "vertical",
                "duration_s": "8-15",
                "description": "assembled build in motion — hands rotating or snapping the "
                               "finished model, natural light, vertical framing",
            }],
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "posts", "gate_action"):
        op.drop_column("posts", "gate_action")
    if _has_column(bind, "posts", "plan_source"):
        op.drop_column("posts", "plan_source")
    if _has_table(bind, "agent_runs"):
        op.drop_table("agent_runs")
    if _has_table(bind, "plans_shadow"):
        op.drop_table("plans_shadow")
