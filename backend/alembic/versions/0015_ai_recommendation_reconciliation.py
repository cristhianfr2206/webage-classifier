"""Add bounded maintenance metadata for advisory recommendation reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_ai_recommendation_reconciliation"
down_revision: str | None = "0014_ai_recommendation_retry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_recommendation_attempts",
        sa.Column("reconciliation_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_recommendation_attempts",
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "ai_recommendation_attempts",
        sa.Column("last_reconciliation_reason", sa.String(120), nullable=False, server_default=""),
    )
    op.add_column("ai_recommendation_attempts", sa.Column("maintenance_lease_id", sa.String(50)))
    op.add_column(
        "ai_recommendation_attempts",
        sa.Column("maintenance_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_ai_recommendation_attempts_maintenance_lease_id",
        "ai_recommendation_attempts",
        ["maintenance_lease_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_recommendation_attempts_maintenance_lease_id", "ai_recommendation_attempts"
    )
    op.drop_column("ai_recommendation_attempts", "maintenance_lease_expires_at")
    op.drop_column("ai_recommendation_attempts", "maintenance_lease_id")
    op.drop_column("ai_recommendation_attempts", "last_reconciliation_reason")
    op.drop_column("ai_recommendation_attempts", "last_reconciled_at")
    op.drop_column("ai_recommendation_attempts", "reconciliation_count")
