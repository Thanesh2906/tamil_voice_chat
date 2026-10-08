"""Add tool_invocations.run_id: trace a tool call back to the run that proposed it."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "run_id" not in {column["name"] for column in inspector.get_columns("tool_invocations")}:
        op.add_column("tool_invocations", sa.Column("run_id", sa.String(length=64), nullable=True))
    if "ix_tool_invocations_run_id" not in {index["name"] for index in inspector.get_indexes("tool_invocations")}:
        op.create_index("ix_tool_invocations_run_id", "tool_invocations", ["run_id"])


def downgrade():
    op.drop_index("ix_tool_invocations_run_id", table_name="tool_invocations")
    op.drop_column("tool_invocations", "run_id")
