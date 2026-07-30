"""Add versioned evaluation, review, and controlled pilot processing."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_evaluation_pilots"
down_revision: str | None = "0005_ai_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _timestamps() -> tuple[sa.Column[sa.DateTime], ...]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    evaluation_status = postgresql.ENUM(
        "DRAFT",
        "PENDING",
        "RUNNING",
        "PAUSED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="evaluation_status",
        create_type=False,
    )
    review_status = postgresql.ENUM(
        "PENDING",
        "ASSIGNED",
        "DISAGREEMENT",
        "RESOLVED",
        "REJECTED",
        "LOCKED",
        name="review_status",
        create_type=False,
    )
    pilot_status = postgresql.ENUM(
        "DRAFT",
        "QUEUED",
        "RUNNING",
        "PAUSED",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "COMPLETED",
        "FAILED",
        name="pilot_status",
        create_type=False,
    )
    for enum_type in (evaluation_status, review_status, pilot_status):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ruleset_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version", sa.String(80), nullable=False, unique=True),
        sa.Column("weights", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("checksum", sa.String(64), nullable=False, unique=True),
        sa.Column("change_notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
    )
    op.create_table(
        "policy_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version", sa.String(80), nullable=False, unique=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("checksum", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
    )
    op.create_table(
        "classifier_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version", sa.String(80), nullable=False, unique=True),
        sa.Column("ruleset_version_id", UUID, sa.ForeignKey("ruleset_versions.id"), nullable=False),
        sa.Column("policy_version_id", UUID, sa.ForeignKey("policy_versions.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("change_notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("activated_by_id", UUID, sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_classifier_versions_active", "classifier_versions", ["is_active"])
    op.create_index(
        "uq_active_classifier_version",
        "classifier_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False, server_default="1"),
        sa.Column(
            "prior_version_id",
            UUID,
            sa.ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
        ),
        sa.Column("checksum", sa.String(64), nullable=False, unique=True),
        sa.Column("source_format", sa.String(10), nullable=False),
        sa.Column("change_notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_by_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("name", "version", name="uq_evaluation_dataset_version"),
        sa.CheckConstraint("source_format IN ('csv','jsonl')"),
    )
    op.create_index("ix_evaluation_datasets_name", "evaluation_datasets", ["name"])
    op.create_index("ix_evaluation_datasets_published", "evaluation_datasets", ["published"])
    op.create_table(
        "evaluation_examples",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "dataset_id",
            UUID,
            sa.ForeignKey("evaluation_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("primary_category", sa.String(80)),
        sa.Column("secondary_categories", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("expected_age", sa.Integer()),
        sa.Column("expected_rating", sa.String(40)),
        sa.Column("expected_blocked", sa.Boolean()),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("adjudicated", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint("dataset_id", "domain", name="uq_evaluation_example_domain"),
        sa.CheckConstraint("expected_age IS NULL OR expected_age BETWEEN 0 AND 120"),
    )
    op.create_index("ix_evaluation_examples_dataset", "evaluation_examples", ["dataset_id"])
    op.create_table(
        "human_labels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "example_id",
            UUID,
            sa.ForeignKey("evaluation_examples.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("expected_age", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint("example_id", "reviewer_id", name="uq_human_label_reviewer"),
        sa.CheckConstraint("expected_age BETWEEN 0 AND 120"),
    )
    op.create_index("ix_human_labels_example", "human_labels", ["example_id"])
    op.execute(
        """
        CREATE FUNCTION prevent_published_dataset_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.published THEN
            RAISE EXCEPTION 'published evaluation datasets are immutable';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_dataset
          BEFORE UPDATE OR DELETE ON evaluation_datasets
          FOR EACH ROW EXECUTE FUNCTION prevent_published_dataset_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_ground_truth_mutation() RETURNS trigger AS $$
        DECLARE selected_dataset uuid;
        BEGIN
          IF TG_TABLE_NAME = 'evaluation_examples' THEN
            selected_dataset := COALESCE(NEW.dataset_id, OLD.dataset_id);
          ELSE
            SELECT dataset_id INTO selected_dataset FROM evaluation_examples
              WHERE id = COALESCE(NEW.example_id, OLD.example_id);
          END IF;
          IF EXISTS (
            SELECT 1 FROM evaluation_datasets
            WHERE id = selected_dataset AND published
          ) THEN
            RAISE EXCEPTION 'published evaluation ground truth is immutable';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_examples
          BEFORE INSERT OR UPDATE OR DELETE ON evaluation_examples
          FOR EACH ROW EXECUTE FUNCTION prevent_published_ground_truth_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_labels
          BEFORE INSERT OR UPDATE OR DELETE ON human_labels
          FOR EACH ROW EXECUTE FUNCTION prevent_published_ground_truth_mutation()
        """
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("dataset_id", UUID, sa.ForeignKey("evaluation_datasets.id"), nullable=False),
        sa.Column(
            "classifier_version_id", UUID, sa.ForeignKey("classifier_versions.id"), nullable=False
        ),
        sa.Column("requested_by_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", evaluation_status, nullable=False, server_default="PENDING"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("task_id", sa.String(50), unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_evaluation_runs_dataset", "evaluation_runs", ["dataset_id"])
    op.create_index("ix_evaluation_runs_classifier", "evaluation_runs", ["classifier_version_id"])
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_table(
        "evaluation_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id", UUID, sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("example_id", UUID, sa.ForeignKey("evaluation_examples.id"), nullable=False),
        sa.Column("predicted_primary", sa.String(80)),
        sa.Column("predicted_secondary", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("predicted_age", sa.Integer()),
        sa.Column("predicted_rating", sa.String(40)),
        sa.Column("predicted_blocked", sa.Boolean()),
        sa.Column("confidence", sa.Integer()),
        sa.Column("source", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_microunits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_code", sa.String(80)),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "example_id", name="uq_evaluation_result"),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 100"),
    )
    op.create_index("ix_evaluation_results_run", "evaluation_results", ["run_id"])
    op.create_table(
        "manual_review_cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("website_id", UUID, sa.ForeignKey("websites.id")),
        sa.Column("example_id", UUID, sa.ForeignKey("evaluation_examples.id")),
        sa.Column("classification_run_id", UUID, sa.ForeignKey("classification_runs.id")),
        sa.Column("status", review_status, nullable=False, server_default="PENDING"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("assigned_to_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("final_category_id", UUID, sa.ForeignKey("categories.id")),
        sa.Column("final_age_policy_id", UUID, sa.ForeignKey("age_policies.id")),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "website_id IS NOT NULL OR example_id IS NOT NULL",
            name="ck_manual_review_subject",
        ),
    )
    op.create_index("ix_manual_review_status", "manual_review_cases", ["status"])
    op.create_index("ix_manual_review_website", "manual_review_cases", ["website_id"])
    op.create_table(
        "manual_review_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "case_id",
            UUID,
            sa.ForeignKey("manual_review_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("category_id", UUID, sa.ForeignKey("categories.id")),
        sa.Column("age_policy_id", UUID, sa.ForeignKey("age_policies.id")),
        sa.Column("notes", sa.String(1000), nullable=False, server_default=""),
        *_timestamps(),
    )
    op.create_index("ix_manual_review_decisions_case", "manual_review_decisions", ["case_id"])
    op.create_table(
        "pilot_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("requested_by_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "classifier_version_id", UUID, sa.ForeignKey("classifier_versions.id"), nullable=False
        ),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("rank_start", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", pilot_status, nullable=False, server_default="DRAFT"),
        sa.Column("capacity_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimate", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("estimate_hash", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("size IN (100, 1000, 10000)", name="ck_pilot_size"),
        sa.CheckConstraint("rank_start BETWEEN 1 AND 1000000"),
        sa.CheckConstraint("capacity_limit BETWEEN 1 AND 1000"),
    )
    op.create_index("ix_pilot_runs_status", "pilot_runs", ["status"])
    op.create_table(
        "pilot_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "pilot_id", UUID, sa.ForeignKey("pilot_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("website_id", UUID, sa.ForeignKey("websites.id"), nullable=False),
        sa.Column("classification_run_id", UUID, sa.ForeignKey("classification_runs.id")),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        *_timestamps(),
        sa.UniqueConstraint("pilot_id", "website_id", name="uq_pilot_item"),
    )
    op.create_index("ix_pilot_items_pilot", "pilot_items", ["pilot_id"])
    op.create_index("ix_pilot_items_status", "pilot_items", ["status"])

    # Immutable baseline captures Milestone 5 behavior; administrators can create successors.
    op.execute(
        """
        INSERT INTO ruleset_versions (id, version, weights, thresholds, checksum, change_notes)
        VALUES (
          '00000000-0000-4000-8000-000000000601', 'milestone-5-baseline',
          jsonb_build_object(
            'education', jsonb_build_object(
              'course', 4, 'learn', 3, 'school', 4, 'university', 4,
              'tutorial', 3, 'lesson', 3
            ),
            'entertainment', jsonb_build_object(
              'game', 3, 'movie', 3, 'music', 3, 'stream', 2, 'video', 2,
              'casino', 7, 'betting', 7
            ),
            'social', jsonb_build_object(
              'community', 3, 'followers', 4, 'profile', 2, 'social', 4,
              'chat', 3, 'forum', 3
            )
          ),
          jsonb_build_object(
            'browser_confidence', 45, 'ai_confidence', 75, 'ai_conflict', 10
          ),
          'a00996e00400de3cbcc7e90b3e914e66bd96b26a29a5e70a34544be7474e6473',
          'Immutable snapshot of the Milestone 5 classifier defaults'
        )
        """
    )
    op.execute(
        """
        INSERT INTO policy_versions (id, version, snapshot, checksum)
        VALUES (
          '00000000-0000-4000-8000-000000000602', 'milestone-5-baseline',
          jsonb_build_object(),
          'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        )
        """
    )
    op.execute(
        """
        INSERT INTO classifier_versions (
          id, version, ruleset_version_id, policy_version_id, is_active,
          change_notes, activated_at
        ) VALUES (
          '00000000-0000-4000-8000-000000000603', 'milestone-5-baseline',
          '00000000-0000-4000-8000-000000000601',
          '00000000-0000-4000-8000-000000000602', true,
          'Seeded reproducibility baseline', now()
        )
        """
    )
    op.add_column(
        "classification_runs",
        sa.Column(
            "classifier_version_id",
            UUID,
            sa.ForeignKey("classifier_versions.id"),
            nullable=False,
            server_default="00000000-0000-4000-8000-000000000603",
        ),
    )
    op.create_index(
        "ix_classification_runs_classifier_version",
        "classification_runs",
        ["classifier_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_classification_runs_classifier_version", table_name="classification_runs")
    op.drop_column("classification_runs", "classifier_version_id")
    for table in (
        "pilot_items",
        "pilot_runs",
        "manual_review_decisions",
        "manual_review_cases",
        "evaluation_results",
        "evaluation_runs",
        "human_labels",
        "evaluation_examples",
        "evaluation_datasets",
        "classifier_versions",
        "policy_versions",
        "ruleset_versions",
    ):
        op.drop_table(table)
    for name in ("pilot_status", "review_status", "evaluation_status"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
    op.execute("DROP FUNCTION IF EXISTS prevent_published_ground_truth_mutation()")
    op.execute("DROP FUNCTION IF EXISTS prevent_published_dataset_mutation()")
