"""Add persistent queue execution state."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_queue_jobs"
down_revision: str | None = "0002_websites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RETRYING'")
        op.execute("ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'CANCELLED'")
    queue_name = postgresql.ENUM(
        "REALTIME", "STANDARD", "MAINTENANCE", name="queue_name", create_type=False
    )
    queue_name.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "classification_runs",
        sa.Column("queue_name", queue_name, server_default="STANDARD", nullable=False),
    )
    op.add_column(
        "classification_runs",
        sa.Column("priority", sa.Integer(), server_default="5", nullable=False),
    )
    op.add_column("classification_runs", sa.Column("task_id", sa.String(50)))
    op.add_column(
        "classification_runs",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "classification_runs",
        sa.Column("max_attempts", sa.Integer(), server_default="4", nullable=False),
    )
    op.add_column(
        "classification_runs",
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("classification_runs", sa.Column("next_retry_at", sa.DateTime(timezone=True)))
    op.add_column("classification_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column(
        "classification_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_classification_runs_task_id", "classification_runs", ["task_id"]
    )
    op.create_check_constraint(
        "ck_classification_run_priority", "classification_runs", "priority BETWEEN 0 AND 9"
    )
    op.create_check_constraint(
        "ck_classification_run_attempts",
        "classification_runs",
        "attempts >= 0 AND max_attempts BETWEEN 1 AND 10",
    )
    op.create_index(
        "ix_classification_runs_schedule",
        "classification_runs",
        ["status", "next_retry_at", "priority"],
    )
    op.drop_index("uq_active_classification_run", table_name="classification_runs")
    op.create_index(
        "uq_active_classification_run",
        "classification_runs",
        ["website_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RETRYING', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_classification_run", table_name="classification_runs")
    op.create_index(
        "uq_active_classification_run",
        "classification_runs",
        ["website_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.drop_index("ix_classification_runs_schedule", table_name="classification_runs")
    op.drop_constraint("ck_classification_run_attempts", "classification_runs", type_="check")
    op.drop_constraint("ck_classification_run_priority", "classification_runs", type_="check")
    op.drop_constraint("uq_classification_runs_task_id", "classification_runs", type_="unique")
    for column in (
        "updated_at",
        "heartbeat_at",
        "next_retry_at",
        "cancel_requested",
        "max_attempts",
        "attempts",
        "task_id",
        "priority",
        "queue_name",
    ):
        op.drop_column("classification_runs", column)
    postgresql.ENUM(name="queue_name").drop(op.get_bind(), checkfirst=True)
