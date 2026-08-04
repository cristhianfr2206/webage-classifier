"""Add advisory-only AI recommendation audit records."""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0013_ai_recommendation_audit"
down_revision: str | None = "0012_dimension_aware_manual_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
UUID = postgresql.UUID(as_uuid=True)

def upgrade() -> None:
    op.create_table("ai_recommendations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("review_case_id", UUID, sa.ForeignKey("manual_review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_snapshot_id", UUID, sa.ForeignKey("manual_review_evidence_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("taxonomy_version_id", UUID, sa.ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False), sa.Column("model", sa.String(120), nullable=False), sa.Column("model_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(80), nullable=False), sa.Column("prompt_checksum", sa.String(64), nullable=False), sa.Column("allowed_label_checksum", sa.String(64), nullable=False), sa.Column("input_checksum", sa.String(64), nullable=False), sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"), sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("confidence", sa.Integer()), sa.Column("uncertainty_reason", sa.String(500), nullable=False, server_default=""), sa.Column("prompt_injection_suspected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer()), sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()), sa.Column("estimated_cost_microunits", sa.Integer()), sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("cancelled_at", sa.DateTime(timezone=True)), sa.Column("failure_code", sa.String(80)), sa.Column("failure_message", sa.String(300), nullable=False, server_default=""), sa.Column("audit_disposition", sa.String(80), nullable=False, server_default="requested"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 100", name="ck_ai_recommendation_confidence"),
        sa.UniqueConstraint("review_case_id", "evidence_snapshot_id", "taxonomy_version_id", "provider", "model", "prompt_version", "allowed_label_checksum", "idempotency_key", name="uq_ai_recommendation_idempotency"),
    )
    for col in ("review_case_id", "evidence_snapshot_id", "taxonomy_version_id", "requested_by_id", "provider", "status"):
        op.create_index(f"ix_ai_recommendations_{col}", "ai_recommendations", [col])
    op.create_table("ai_recommendation_usage_reservations",
        sa.Column("id", UUID, primary_key=True), sa.Column("recommendation_id", UUID, sa.ForeignKey("ai_recommendations.id", ondelete="CASCADE"), nullable=False), sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=False), sa.Column("review_case_id", UUID, sa.ForeignKey("manual_review_cases.id", ondelete="CASCADE"), nullable=False), sa.Column("reserved_cost_microunits", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.UniqueConstraint("recommendation_id", name="uq_ai_recommendation_reservation"))
    for col in ("recommendation_id", "reviewer_id", "review_case_id", "created_at"):
        op.create_index(f"ix_ai_recommendation_usage_reservations_{col}", "ai_recommendation_usage_reservations", [col])

def downgrade() -> None:
    op.drop_table("ai_recommendation_usage_reservations")
    op.drop_table("ai_recommendations")
