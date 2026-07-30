"""Add provider-independent AI classification metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_ai_classification"
down_revision: str | None = "0004_browser_inspection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE queue_name ADD VALUE IF NOT EXISTS 'AI_REALTIME'")
        op.execute("ALTER TYPE queue_name ADD VALUE IF NOT EXISTS 'AI'")
        op.execute("ALTER TYPE classification_source ADD VALUE IF NOT EXISTS 'AI'")
    status = postgresql.ENUM(
        "PENDING",
        "RETRYING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "REVIEW_REQUIRED",
        name="ai_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "ai_configuration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider", sa.String(80), nullable=False, server_default="disabled"),
        sa.Column("model", sa.String(120), nullable=False, server_default=""),
        sa.Column("confidence_threshold", sa.Integer(), nullable=False, server_default="75"),
        sa.Column("conflict_threshold", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("screenshot_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("daily_request_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "monthly_cost_limit_microunits", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("confidence_threshold BETWEEN 0 AND 100"),
        sa.CheckConstraint("conflict_threshold BETWEEN 0 AND 100"),
    )
    op.add_column(
        "age_policies", sa.Column("rating", sa.String(40), nullable=False, server_default="unrated")
    )
    op.add_column(
        "age_policies",
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "age_policies",
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "age_policies", sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
    )
    op.create_table(
        "ai_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("classification_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "website_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("websites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", status, nullable=False, server_default="PENDING"),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("task_id", sa.String(50), unique=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_started_at", sa.DateTime(timezone=True)),
        sa.Column("request_finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("input_hash", sa.String(64)),
        sa.Column("input_character_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_token_count", sa.Integer()),
        sa.Column("output_token_count", sa.Integer()),
        sa.Column("provider_request_id", sa.String(120)),
        sa.Column("confidence", sa.Integer()),
        sa.Column(
            "prompt_injection_suspected", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("validation_status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("failure_message", sa.String(300)),
        sa.Column("usage_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("estimated_cost_microunits", sa.Integer()),
        sa.Column("primary_category", sa.String(80)),
        sa.Column("secondary_categories", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("intended_audience", sa.String(200), nullable=False, server_default=""),
        sa.Column("uncertainty_reason", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "manual_review_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("promoted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 100", name="ck_ai_confidence"
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND max_retries BETWEEN 0 AND 5", name="ck_ai_retries"
        ),
    )
    for name, column in (
        ("ix_ai_classifications_status", "status"),
        ("ix_ai_classifications_provider", "provider"),
        ("ix_ai_classifications_created_at", "created_at"),
        ("ix_ai_classifications_run_id", "run_id"),
        ("ix_ai_classifications_website_id", "website_id"),
        ("ix_ai_classifications_manual_review", "manual_review_required"),
    ):
        op.create_index(name, "ai_classifications", [column])
    op.create_index(
        "uq_active_ai_website",
        "ai_classifications",
        ["website_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','RETRYING','RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_ai_website", table_name="ai_classifications")
    for name in (
        "ix_ai_classifications_manual_review",
        "ix_ai_classifications_website_id",
        "ix_ai_classifications_run_id",
        "ix_ai_classifications_created_at",
        "ix_ai_classifications_provider",
        "ix_ai_classifications_status",
    ):
        op.drop_index(name, table_name="ai_classifications")
    op.drop_table("ai_classifications")
    op.execute("DROP TABLE IF EXISTS ai_configuration")
    postgresql.ENUM(name="ai_status").drop(op.get_bind(), checkfirst=True)
    op.drop_column("age_policies", "priority")
    op.drop_column("age_policies", "review_required")
    op.drop_column("age_policies", "blocked")
    op.drop_column("age_policies", "rating")
