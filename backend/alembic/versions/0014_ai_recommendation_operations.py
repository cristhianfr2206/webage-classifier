"""Add advisory recommendation attempts, dispatches, and settlement state."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_ai_recommendation_operations"
down_revision: str | None = "0013_ai_recommendation_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "ai_recommendation_usage_reservations",
        sa.Column("status", sa.String(20), nullable=False, server_default="reserved"),
    )
    op.add_column(
        "ai_recommendation_usage_reservations",
        sa.Column("settled_cost_microunits", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_recommendation_usage_reservations",
        sa.Column("settled_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_recommendation_usage_reservations",
        sa.Column("settled_output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_recommendation_usage_reservations", sa.Column("settled_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_ai_recommendation_usage_reservations_status",
        "ai_recommendation_usage_reservations",
        ["status"],
    )
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
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("failure_code", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "recommendation_id", "attempt_number", name="uq_ai_recommendation_attempt"
        ),
    )
    op.create_index(
        "ix_ai_recommendation_attempts_recommendation_id",
        "ai_recommendation_attempts",
        ["recommendation_id"],
    )
    op.create_index(
        "ix_ai_recommendation_attempts_status", "ai_recommendation_attempts", ["status"]
    )
    op.create_table(
        "ai_recommendation_dispatches",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "recommendation_id",
            UUID,
            sa.ForeignKey("ai_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(50), nullable=False, unique=True),
        sa.Column("queue_name", sa.String(40), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="reserved"),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "recommendation_id", "attempt_number", name="uq_ai_recommendation_active_dispatch"
        ),
    )
    op.create_index(
        "ix_ai_recommendation_dispatches_recommendation_id",
        "ai_recommendation_dispatches",
        ["recommendation_id"],
    )


def downgrade() -> None:
    op.drop_table("ai_recommendation_dispatches")
    op.drop_table("ai_recommendation_attempts")
    op.drop_index(
        "ix_ai_recommendation_usage_reservations_status",
        table_name="ai_recommendation_usage_reservations",
    )
    for name in (
        "settled_at",
        "settled_output_tokens",
        "settled_input_tokens",
        "settled_cost_microunits",
        "status",
    ):
        op.drop_column("ai_recommendation_usage_reservations", name)
