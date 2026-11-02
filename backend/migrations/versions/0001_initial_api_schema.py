"""Initial control API schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repo", sa.String(length=512), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("merge_policy", sa.String(length=32), nullable=False),
        sa.Column("model_profile", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "merge_policy IN ('human', 'agent-after-approval', 'agent')",
            name="ck_sessions_merge_policy",
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=256), nullable=False),
        sa.Column("repo", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.String(length=64)),
        sa.Column("branch", sa.String(length=256)),
        sa.Column("sandbox_image_digest", sa.String(length=256)),
        sa.Column("model_aliases", JSON_DOCUMENT, nullable=False),
        sa.Column("skill_versions", JSON_DOCUMENT, nullable=False),
        sa.Column("prompt_hash", sa.String(length=128)),
        sa.Column("budgets", JSON_DOCUMENT, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("human_touches", sa.Integer(), nullable=False),
        sa.Column("artifacts", JSON_DOCUMENT, nullable=False),
        sa.Column("last_event_seq", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'handoff', "
            "'succeeded', 'failed')",
            name="ck_runs_status",
        ),
    )
    op.create_index("ix_runs_session_id", "runs", ["session_id"])
    op.create_index("ix_runs_trigger", "runs", ["trigger"])
    op.create_table(
        "events",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("agent", sa.String(length=100)),
        sa.Column("data", JSON_DOCUMENT, nullable=False),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("args", JSON_DOCUMENT, nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_approvals_status",
        ),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("events")
    op.drop_index("ix_runs_trigger", table_name="runs")
    op.drop_index("ix_runs_session_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
