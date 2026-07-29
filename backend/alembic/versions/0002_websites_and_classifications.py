"""Add websites, imports, classification runs, and history."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_websites"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    run_status = postgresql.ENUM(
        "PENDING", "RUNNING", "COMPLETED", "FAILED", name="run_status", create_type=False
    )
    classification_source = postgresql.ENUM(
        "RULES", "MANUAL", name="classification_source", create_type=False
    )
    run_status.create(op.get_bind(), checkfirst=True)
    classification_source.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "categories",
        sa.Column("age_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_categories_age_policy_id",
        "categories",
        "age_policies",
        ["age_policy_id"],
        ["id"],
    )
    op.create_table(
        "websites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("registrable_domain", sa.String(253), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("tranco_rank", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_websites_domain", "websites", ["domain"], unique=True)
    op.create_index("ix_websites_registrable_domain", "websites", ["registrable_domain"])
    op.create_index("ix_websites_tranco_rank", "websites", ["tranco_rank"])
    op.create_table(
        "tranco_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("requested_limit", sa.Integer()),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "classification_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("website_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["website_id"], ["websites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_classification_runs_website_id", "classification_runs", ["website_id"])
    op.create_index("ix_classification_runs_status", "classification_runs", ["status"])
    op.create_index(
        "uq_active_classification_run",
        "classification_runs",
        ["website_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_table(
        "website_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("website_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("age_policy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source", classification_source, nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("text_excerpt", sa.String(2000), nullable=False),
        sa.Column("overridden_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_confidence_range"),
        sa.ForeignKeyConstraint(["age_policy_id"], ["age_policies.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
        sa.ForeignKeyConstraint(["overridden_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["classification_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["website_id"], ["websites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_website_classifications_website_id", "website_classifications", ["website_id"]
    )
    op.create_index("ix_website_classifications_run_id", "website_classifications", ["run_id"])
    op.create_index(
        "ix_website_classifications_created_at", "website_classifications", ["created_at"]
    )
    op.create_index(
        "ix_website_classification_history",
        "website_classifications",
        ["website_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("website_classifications")
    op.drop_index("uq_active_classification_run", table_name="classification_runs")
    op.drop_table("classification_runs")
    op.drop_table("tranco_imports")
    op.drop_table("websites")
    op.drop_constraint("fk_categories_age_policy_id", "categories", type_="foreignkey")
    op.drop_column("categories", "age_policy_id")
    postgresql.ENUM(name="classification_source").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="run_status").drop(op.get_bind(), checkfirst=True)
