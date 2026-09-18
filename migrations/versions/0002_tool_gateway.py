"""Add tool_invocations for the approval-gated tool gateway (Phase 4)."""

from alembic import op

from packages.db import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # create_all only creates tables that don't already exist, so this adds
    # tool_invocations (and any other new ORM table) without touching 0001's tables.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.tables["tool_invocations"].drop(bind=op.get_bind())
