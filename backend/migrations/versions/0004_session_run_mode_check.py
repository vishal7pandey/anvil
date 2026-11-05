"""Restrict sessions.mode and runs.mode to the two known values (ANVIL-98).

An unknown mode string would otherwise reach approval_policy, which is now default-deny too —
but the schema layer (Literal) plus this CHECK keep a bad mode out entirely.
"""

from alembic import op


revision = "0004_session_run_mode_check"
down_revision = "0003_run_record_network"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.create_check_constraint(
            "ck_sessions_mode",
            "mode IN ('interactive', 'autonomous')",
        )
    with op.batch_alter_table("runs") as batch_op:
        batch_op.create_check_constraint(
            "ck_runs_mode",
            "mode IN ('interactive', 'autonomous')",
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_mode", type_="check")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("ck_sessions_mode", type_="check")
