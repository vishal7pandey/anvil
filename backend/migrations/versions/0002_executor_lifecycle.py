"""Extend run lifecycle and executor metadata."""

from alembic import op
import sqlalchemy as sa

revision = "0002_executor_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_status", type_="check")
        batch_op.add_column(
            sa.Column("base_ref", sa.String(length=256), nullable=False, server_default="main")
        )
        batch_op.add_column(sa.Column("jira_key", sa.String(length=64), nullable=True))
        batch_op.create_check_constraint(
            "ck_runs_status",
            "status IN ('queued', 'running', 'waiting_approval', 'handoff', "
            "'succeeded', 'failed', 'cancelled')",
        )
        batch_op.alter_column("base_ref", server_default=None)

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_column("resumed_at")

    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_status", type_="check")
        batch_op.drop_column("base_ref")
        batch_op.drop_column("jira_key")
        batch_op.create_check_constraint(
            "ck_runs_status",
            "status IN ('queued', 'running', 'waiting_approval', 'handoff', "
            "'succeeded', 'failed')",
        )
