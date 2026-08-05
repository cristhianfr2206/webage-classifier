"""Add persisted advisory recommendation attempts and dispatch reservations."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_ai_recommendation_retry"
down_revision: str | None = "0013_ai_recommendation_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "ai_recommendation_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "recommendation_id",
            UUID,
            sa.ForeignKey("ai_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("failure_class", sa.String(32)),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("task_id", sa.String(50), unique=True),
        sa.Column("queue_name", sa.String(40)),
        sa.Column("requested_by_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("retry_of_attempt_id", UUID, sa.ForeignKey("ai_recommendation_attempts.id")),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("failure_message", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "recommendation_id", "attempt_number", name="uq_ai_recommendation_attempt"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_ai_recommendation_attempt_number"),
    )
    op.create_index(
        "ix_ai_recommendation_attempts_recommendation_id",
        "ai_recommendation_attempts",
        ["recommendation_id"],
    )
    op.create_table(
        "ai_recommendation_dispatch_reservations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "recommendation_attempt_id",
            UUID,
            sa.ForeignKey("ai_recommendation_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dispatch_key", sa.String(64), nullable=False, unique=True),
        sa.Column("task_id", sa.String(50), nullable=False, unique=True),
        sa.Column("queue_name", sa.String(40), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="reserved"),
        sa.Column(
            "reserved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.String(160), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "recommendation_attempt_id", name="uq_ai_recommendation_attempt_dispatch"
        ),
        sa.CheckConstraint(
            "queue_name IN ('ai', 'ai_realtime')",
            name="ck_ai_recommendation_dispatch_queue",
        ),
    )
    op.create_index(
        "ix_ai_rec_dispatch_attempt",
        "ai_recommendation_dispatch_reservations",
        ["recommendation_attempt_id"],
    )


def downgrade() -> None:
    op.drop_table("ai_recommendation_dispatch_reservations")
    op.drop_table("ai_recommendation_attempts")
