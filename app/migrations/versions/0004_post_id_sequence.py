"""v4 Stage 2b §0: move post_id allocation OUT of `config` into a sequence.

Stage 2a's injection path incremented a `config` row, which made a tool write `config` —
a breach of the §2 invariant. The remedy is the one used for endpoint_prices: move the
state out, never weaken the rule. After this migration nothing reads `post_id_counter`;
the row is deleted so it cannot be read by accident.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.post_ids import ensure_allocator

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Seeded from max(existing post_id suffix) + 1 or 1482, whichever is higher — an id
    # already in use can never be handed out again.
    start = ensure_allocator(bind)
    print(f"post_id allocator ready; next id = post_{start}")

    # Dead data: delete it so no code path can read it after this point.
    config = sa.table("config", sa.column("key", sa.Text()))
    bind.execute(config.delete().where(config.c.key == "post_id_counter"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.engine.dialect.name == "postgresql":
        op.execute("DROP SEQUENCE IF EXISTS post_id_seq")
    else:
        op.execute("DROP TABLE IF EXISTS post_id_seq")
