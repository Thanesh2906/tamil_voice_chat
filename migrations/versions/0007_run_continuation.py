"""Durable, fenced run checkpoints and cancellation; preserve existing records."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    additions = (
        sa.Column("checkpoint_json", sa.Text(), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("agent_runs", column)
    # awaiting_approval is 17 characters; SQLite did not enforce the old width.
    if getattr(columns["status"]["type"], "length", None) != 32:
        with op.batch_alter_table("agent_runs") as batch:
            batch.alter_column("status", existing_type=sa.String(16), type_=sa.String(32))


def downgrade():
    # Never truncate an awaiting_approval status during downgrade.
    with op.batch_alter_table("agent_runs") as batch:
        for name in ("revision", "cancellation_requested_at", "lease_expires_at", "claim_token", "checkpoint_json"):
            batch.drop_column(name)
