"""Add additive, auditable dimension-aware manual-review storage.

Legacy manual-review rows and endpoints remain valid.  No row is backfilled,
no review case is routed automatically, and this revision has no task or
classification side effects.
"""

# ruff: noqa: S608

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_dimension_aware_manual_review"
down_revision: str | None = "0011_feed_label_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # The repository's historical Alembic version table used varchar(32),
    # while this required, descriptive revision identifier is longer.  Widen
    # metadata before Alembic writes the new revision; no application data is
    # affected and the wider type remains compatible with every prior id.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    review_disposition = postgresql.ENUM(
        "PENDING_REVIEW",
        "IN_REVIEW",
        "RESOLVED",
        "UNRESOLVED",
        "NON_CONSUMER_INFRASTRUCTURE",
        "UNREACHABLE",
        "SAFETY_BLOCKED",
        "CANCELLED",
        name="review_disposition",
        create_type=False,
    )
    review_decision_state = postgresql.ENUM(
        "ACCEPTED", "REJECTED", "OVERRIDDEN", name="review_decision_state", create_type=False
    )
    review_label_role = postgresql.ENUM(
        "PRIMARY", "SECONDARY", "SUPPORTING", "REJECTED_CANDIDATE",
        name="review_label_role",
        create_type=False,
    )
    for enum_type in (review_disposition, review_decision_state, review_label_role):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "manual_review_cases",
        sa.Column(
            "taxonomy_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column(
            "source_assessment_id",
            UUID,
            sa.ForeignKey("classification_assessments.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("manual_review_cases", sa.Column("disposition", review_disposition))
    op.add_column(
        "manual_review_cases",
        sa.Column("conflict_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column("claimed_by_id", UUID, sa.ForeignKey("users.id")),
    )
    op.add_column("manual_review_cases", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("manual_review_cases", sa.Column("claim_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "manual_review_cases",
        sa.Column("locked_by_id", UUID, sa.ForeignKey("users.id")),
    )
    op.add_column("manual_review_cases", sa.Column("locked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "manual_review_cases",
        sa.Column("lock_reason", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column("reopened_by_id", UUID, sa.ForeignKey("users.id")),
    )
    op.add_column("manual_review_cases", sa.Column("reopened_at", sa.DateTime(timezone=True)))
    op.add_column(
        "manual_review_cases",
        sa.Column("reopen_reason", sa.String(500), nullable=False, server_default=""),
    )
    op.add_column(
        "manual_review_cases",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_check_constraint(
        "ck_manual_review_case_revision", "manual_review_cases", "revision >= 0"
    )
    for index_name, column in (
        ("ix_manual_review_cases_taxonomy_version_id", "taxonomy_version_id"),
        ("ix_manual_review_cases_source_assessment_id", "source_assessment_id"),
        ("ix_manual_review_cases_disposition", "disposition"),
        ("ix_manual_review_cases_priority", "priority"),
        ("ix_manual_review_cases_claimed_by_id", "claimed_by_id"),
        ("ix_manual_review_cases_claim_expires_at", "claim_expires_at"),
    ):
        op.create_index(index_name, "manual_review_cases", [column])

    op.create_table(
        "manual_review_evidence_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "review_case_id",
            UUID,
            sa.ForeignKey("manual_review_cases.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_classification_run_id",
            UUID,
            sa.ForeignKey("classification_runs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "browser_inspection_id",
            UUID,
            sa.ForeignKey("browser_inspections.id", ondelete="SET NULL"),
        ),
        sa.Column("feed_evidence_reference", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "source_assessment_id",
            UUID,
            sa.ForeignKey("classification_assessments.id", ondelete="SET NULL"),
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False, unique=True),
        sa.Column("provenance", sa.String(1000), nullable=False, server_default=""),
        sa.Column("payload_schema_version", sa.String(20), nullable=False, server_default="1"),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("char_length(evidence_checksum) = 64", name="ck_review_snapshot_checksum"),
        sa.CheckConstraint(
            "payload_size_bytes BETWEEN 2 AND 65536", name="ck_review_snapshot_size"
        ),
    )
    for index_name, column in (
        ("ix_review_evidence_snapshots_review_case_id", "review_case_id"),
        ("ix_review_evidence_snapshots_source_classification_run_id", "source_classification_run_id"),
        ("ix_review_evidence_snapshots_browser_inspection_id", "browser_inspection_id"),
        ("ix_review_evidence_snapshots_source_assessment_id", "source_assessment_id"),
    ):
        op.create_index(index_name, "manual_review_evidence_snapshots", [column])
    op.add_column(
        "manual_review_cases",
        sa.Column(
            "evidence_snapshot_id",
            UUID,
            sa.ForeignKey("manual_review_evidence_snapshots.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_manual_review_cases_evidence_snapshot_id", "manual_review_cases", ["evidence_snapshot_id"]
    )

    taxonomy_dimension = postgresql.ENUM(
        "CONTENT", "SECURITY", "SCOPE", name="taxonomy_dimension", create_type=False
    )
    taxonomy_dimension.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "manual_review_label_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "review_case_id",
            UUID,
            sa.ForeignKey("manual_review_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "taxonomy_label_id",
            UUID,
            sa.ForeignKey("taxonomy_labels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "taxonomy_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dimension", taxonomy_dimension, nullable=False),
        sa.Column("role", review_label_role, nullable=False),
        sa.Column("state", review_decision_state, nullable=False),
        sa.Column("reviewer_confidence", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.String(2000), nullable=False, server_default=""),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "reviewer_confidence BETWEEN 0 AND 100", name="ck_review_decision_confidence"
        ),
    )
    for index_name, column in (
        ("ix_manual_review_label_decisions_review_case_id", "review_case_id"),
        ("ix_manual_review_label_decisions_taxonomy_label_id", "taxonomy_label_id"),
        ("ix_manual_review_label_decisions_taxonomy_version_id", "taxonomy_version_id"),
        ("ix_manual_review_label_decisions_state", "state"),
        ("ix_manual_review_label_decisions_reviewer_id", "reviewer_id"),
        ("ix_manual_review_label_decisions_superseded_at", "superseded_at"),
    ):
        op.create_index(index_name, "manual_review_label_decisions", [column])
    op.create_index(
        "uq_review_active_primary_content_decision",
        "manual_review_label_decisions",
        ["review_case_id"],
        unique=True,
        postgresql_where=sa.text("state = 'ACCEPTED' AND role = 'PRIMARY' AND superseded_at IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_review_snapshot_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'review_evidence_snapshot_is_immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_review_evidence_snapshot
          BEFORE UPDATE OR DELETE ON manual_review_evidence_snapshots
          FOR EACH ROW EXECUTE FUNCTION prevent_review_snapshot_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_manual_review_label_decision() RETURNS trigger AS $$
        DECLARE
            case_version uuid;
            label_version uuid;
            label_dimension taxonomy_dimension;
            label_status taxonomy_label_status;
            case_locked boolean;
        BEGIN
            SELECT taxonomy_version_id, locked INTO case_version, case_locked
            FROM manual_review_cases WHERE id = NEW.review_case_id;
            IF NOT FOUND OR case_locked THEN
                RAISE EXCEPTION 'locked_review_case_is_immutable';
            END IF;
            SELECT taxonomy_version_id, dimension, status
              INTO label_version, label_dimension, label_status
              FROM taxonomy_labels WHERE id = NEW.taxonomy_label_id;
            IF NOT FOUND OR case_version IS NULL OR case_version <> NEW.taxonomy_version_id
               OR label_version <> NEW.taxonomy_version_id THEN
                RAISE EXCEPTION 'review_label_taxonomy_mismatch';
            END IF;
            IF label_dimension <> NEW.dimension THEN
                RAISE EXCEPTION 'review_label_dimension_mismatch';
            END IF;
            IF NEW.role = 'PRIMARY' AND (
                NEW.state <> 'ACCEPTED' OR label_dimension <> 'CONTENT' OR label_status <> 'ACTIVE'
            ) THEN
                RAISE EXCEPTION 'review_primary_must_be_active_content';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_manual_review_label_decision_before_write
          BEFORE INSERT OR UPDATE ON manual_review_label_decisions
          FOR EACH ROW EXECUTE FUNCTION validate_manual_review_label_decision()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_locked_manual_review_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.locked AND NEW.locked THEN
                RAISE EXCEPTION 'locked_review_case_is_immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_locked_manual_review_case
          BEFORE UPDATE ON manual_review_cases
          FOR EACH ROW EXECUTE FUNCTION prevent_locked_manual_review_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_locked_manual_review_case ON manual_review_cases")
    op.execute("DROP FUNCTION prevent_locked_manual_review_mutation()")
    op.execute("DROP TRIGGER validate_manual_review_label_decision_before_write ON manual_review_label_decisions")
    op.execute("DROP FUNCTION validate_manual_review_label_decision()")
    op.execute("DROP TRIGGER immutable_review_evidence_snapshot ON manual_review_evidence_snapshots")
    op.execute("DROP FUNCTION prevent_review_snapshot_mutation()")
    op.drop_index("uq_review_active_primary_content_decision", table_name="manual_review_label_decisions")
    op.drop_table("manual_review_label_decisions")
    op.drop_column("manual_review_cases", "evidence_snapshot_id")
    op.drop_table("manual_review_evidence_snapshots")
    for index_name in (
        "ix_manual_review_cases_claim_expires_at",
        "ix_manual_review_cases_claimed_by_id",
        "ix_manual_review_cases_priority",
        "ix_manual_review_cases_disposition",
        "ix_manual_review_cases_source_assessment_id",
        "ix_manual_review_cases_taxonomy_version_id",
    ):
        op.drop_index(index_name, table_name="manual_review_cases")
    op.drop_constraint("ck_manual_review_case_revision", "manual_review_cases", type_="check")
    for column in (
        "updated_at", "reopen_reason", "reopened_at", "reopened_by_id", "lock_reason", "locked_at",
        "locked_by_id", "claim_expires_at", "claimed_at", "claimed_by_id", "priority", "conflict_flags",
        "disposition", "revision", "source_assessment_id", "taxonomy_version_id",
    ):
        op.drop_column("manual_review_cases", column)
    op.execute("DROP TYPE review_label_role")
    op.execute("DROP TYPE review_decision_state")
    op.execute("DROP TYPE review_disposition")
