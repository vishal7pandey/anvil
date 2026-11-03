"""Persist network policy and reproducibility records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_run_record_network"
down_revision = "0002_executor_lifecycle"
branch_labels = None
depends_on = None
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column("network", sa.String(length=16), nullable=False, server_default="offline")
        )
        batch_op.add_column(
            sa.Column(
                "run_record",
                JSON_DOCUMENT,
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_runs_network",
            "network IN ('offline', 'online')",
        )
        batch_op.alter_column("network", server_default=None)
        batch_op.alter_column("run_record", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_network", type_="check")
        batch_op.drop_column("network")
        batch_op.drop_column("run_record")
