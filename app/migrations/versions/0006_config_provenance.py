"""config provenance — who set this value.

`SUPERSEDED_DEFAULTS` corrects a shipped default that nobody chose. Without provenance it
cannot tell a stored 500 that came from an old seed from a stored 500 the operator picked,
so it would silently overwrite operator intent — config-silence, inside the mechanism built
to prevent it.

`set_by` is 'seed' when a value came from OPERATOR_CONSTANTS and 'operator' when it came
from `artec config set` or an explicit --file override. Rows that predate this column are
NULL = UNKNOWN, and unknown is never overwritten: it is REPORTED, so the operator decides.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("config")}
    if "set_by" not in existing:
        op.add_column("config", sa.Column("set_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("config", "set_by")
