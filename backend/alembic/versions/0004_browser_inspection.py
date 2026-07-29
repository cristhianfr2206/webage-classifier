"""Add isolated browser inspection metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_browser_inspection"
down_revision: str | None = "0003_queue_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE queue_name ADD VALUE IF NOT EXISTS 'BROWSER_REALTIME'")
        op.execute("ALTER TYPE queue_name ADD VALUE IF NOT EXISTS 'BROWSER'")
        op.execute("ALTER TYPE classification_source ADD VALUE IF NOT EXISTS 'RENDERED'")
        op.execute("ALTER TYPE classification_source ADD VALUE IF NOT EXISTS 'SCREENSHOT'")
    browser_status = postgresql.ENUM(
        "PENDING",
        "RETRYING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="browser_status",
        create_type=False,
    )
    browser_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "browser_inspections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classification_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", browser_status, nullable=False, server_default="PENDING"),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("task_id", sa.String(50), unique=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("rendered_final_url", sa.String(2048)),
        sa.Column("rendered_title", sa.String(500), nullable=False, server_default=""),
        sa.Column("rendered_text_sample", sa.String(50000), nullable=False, server_default=""),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transferred_byte_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("artifact_id", sa.String(64), unique=True),
        sa.Column("artifact_expires_at", sa.DateTime(timezone=True)),
        sa.Column("browser_version", sa.String(80)),
        sa.Column("playwright_version", sa.String(40)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts BETWEEN 1 AND 6", name="ck_browser_attempts"
        ),
        sa.CheckConstraint(
            "request_count >= 0 AND transferred_byte_count >= 0 AND blocked_request_count >= 0",
            name="ck_browser_counts",
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_browser_duration"),
    )
    op.create_index("ix_browser_inspections_status", "browser_inspections", ["status"])
    op.create_index("ix_browser_inspections_run_id", "browser_inspections", ["run_id"])
    op.create_index(
        "ix_browser_inspections_artifact_expires_at",
        "browser_inspections",
        ["artifact_expires_at"],
    )
    op.create_index(
        "uq_active_browser_website",
        "browser_inspections",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RETRYING', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_browser_website", table_name="browser_inspections")
    op.drop_index("ix_browser_inspections_artifact_expires_at", table_name="browser_inspections")
    op.drop_index("ix_browser_inspections_run_id", table_name="browser_inspections")
    op.drop_index("ix_browser_inspections_status", table_name="browser_inspections")
    op.drop_table("browser_inspections")
    postgresql.ENUM(name="browser_status").drop(op.get_bind(), checkfirst=True)
