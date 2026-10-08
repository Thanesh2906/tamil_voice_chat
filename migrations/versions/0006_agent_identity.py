"""Persist selected and executing agent identities without changing existing runs."""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # Early migrations used current Base.metadata.create_all, so fresh installs
    # may already contain these columns. Existing installations receive defaults.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    for name in ("agent_id", "execution_agent_id"):
        if name not in columns:
            op.add_column("agent_runs", sa.Column(name, sa.String(32), nullable=False, server_default="manager"))
    if "conversation_id" not in columns:
        # Batch alteration supports SQLite and creates the FK in PostgreSQL.
        with op.batch_alter_table("agent_runs") as batch:
            batch.add_column(sa.Column("conversation_id", sa.String(64), nullable=True))
            batch.create_foreign_key("fk_agent_run_conversation", "conversations", ["conversation_id"], ["id"])


def downgrade():
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("conversation_id")
        batch.drop_column("execution_agent_id")
        batch.drop_column("agent_id")
