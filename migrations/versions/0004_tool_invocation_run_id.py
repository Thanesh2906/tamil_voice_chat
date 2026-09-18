"""Add tool_invocations.run_id: trace a tool call back to the run that proposed it."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tool_invocations", sa.Column("run_id", sa.String(length=64), nullable=True))
    op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])


def downgrade():
    op.drop_index("ix_tool_invocations_run_id", table_name="tool_invocations")
    op.drop_column("tool_invocations", "run_id")
