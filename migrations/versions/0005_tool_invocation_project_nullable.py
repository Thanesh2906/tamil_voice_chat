"""tool_invocations.project_id becomes nullable: a run-proposed tool call can
come from a project-less "personal" run (services/manager), same as
agent_runs.project_id already allows."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tool_invocations") as batch:
        batch.alter_column("project_id", existing_type=sa.String(length=64), nullable=True)


def downgrade():
    with op.batch_alter_table("tool_invocations") as batch:
        batch.alter_column("project_id", existing_type=sa.String(length=64), nullable=False)
