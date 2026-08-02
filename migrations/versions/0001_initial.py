"""initial schema — all tables + v_brief view

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""
from __future__ import annotations

from alembic import op

from app.models import DROP_V_BRIEF_SQL, V_BRIEF_SQL, Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The models are the single source of truth for the initial schema; this migration
    # materializes them verbatim, then adds the v_brief view.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(V_BRIEF_SQL)


def downgrade() -> None:
    op.execute(DROP_V_BRIEF_SQL)
    Base.metadata.drop_all(bind=op.get_bind())
