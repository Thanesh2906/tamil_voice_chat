"""Add agent_runs and run_events for durable manager runs (Phase 2)."""

from alembic import op

from packages.db import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.tables["run_events"].drop(bind=op.get_bind())
    Base.metadata.tables["agent_runs"].drop(bind=op.get_bind())
