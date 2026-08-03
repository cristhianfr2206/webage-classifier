"""Add additive multidimensional taxonomy storage.

Phase 1 creates no taxonomy labels, backfills no legacy classifications, and
does not alter classification behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_taxonomy_dimensions"
down_revision: str | None = "0006c_pilot_stabilization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    taxonomy_version_status = postgresql.ENUM(
        "DRAFT", "PUBLISHED", "RETIRED", name="taxonomy_version_status", create_type=False
    )
    taxonomy_label_status = postgresql.ENUM(
        "ACTIVE", "DEPRECATED", name="taxonomy_label_status", create_type=False
    )
    taxonomy_dimension = postgresql.ENUM(
        "CONTENT", "SECURITY", "SCOPE", name="taxonomy_dimension", create_type=False
    )
    assessment_disposition = postgresql.ENUM(
        "UNRESOLVED",
        "CLASSIFIED",
        "UNCATEGORIZED",
        "NON_CONSUMER_INFRASTRUCTURE",
        "UNREACHABLE",
        "SAFETY_REJECTED",
        "REVIEW_REQUIRED",
        "FAILED",
        "CANCELLED",
        name="assessment_disposition",
        create_type=False,
    )
    assessment_label_role = postgresql.ENUM(
        "PRIMARY", "SECONDARY", "EVIDENCE", name="assessment_label_role", create_type=False
    )
    assessment_label_source = postgresql.ENUM(
        "RULES",
        "STATIC",
        "RENDERED",
        "SCREENSHOT",
        "UT1",
        "INFRASTRUCTURE",
        "AI",
        "MANUAL",
        name="assessment_label_source",
        create_type=False,
    )
    for enum_type in (
        taxonomy_version_status,
        taxonomy_label_status,
        taxonomy_dimension,
        assessment_disposition,
        assessment_label_role,
        assessment_label_source,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "taxonomy_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version", sa.String(80), nullable=False, unique=True),
        sa.Column("status", taxonomy_version_status, nullable=False, server_default="DRAFT"),
        sa.Column("checksum", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "parent_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("change_notes", sa.String(1000), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_taxonomy_versions_status", "taxonomy_versions", ["status"])
    op.create_index(
        "ix_taxonomy_versions_parent_version_id", "taxonomy_versions", ["parent_version_id"]
    )

    op.create_table(
        "taxonomy_labels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "taxonomy_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", taxonomy_dimension, nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("definition", sa.String(2000), nullable=False, server_default=""),
        sa.Column("status", taxonomy_label_status, nullable=False, server_default="ACTIVE"),
        sa.Column(
            "default_review_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "default_block_recommended", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("taxonomy_version_id", "slug", name="uq_taxonomy_label_version_slug"),
    )
    op.create_index(
        "ix_taxonomy_labels_taxonomy_version_id", "taxonomy_labels", ["taxonomy_version_id"]
    )
    op.create_index("ix_taxonomy_labels_dimension", "taxonomy_labels", ["dimension"])
    op.create_index("ix_taxonomy_labels_status", "taxonomy_labels", ["status"])

    op.create_table(
        "classification_assessments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id",
            UUID,
            sa.ForeignKey("classification_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "website_id", UUID, sa.ForeignKey("websites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "taxonomy_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "terminal_disposition",
            assessment_disposition,
            nullable=False,
            server_default="UNRESOLVED",
        ),
        sa.Column(
            "primary_content_label_id",
            UUID,
            sa.ForeignKey("taxonomy_labels.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    for index_name, column in (
        ("ix_classification_assessments_run_id", "run_id"),
        ("ix_classification_assessments_website_id", "website_id"),
        ("ix_classification_assessments_taxonomy_version_id", "taxonomy_version_id"),
        ("ix_classification_assessments_terminal_disposition", "terminal_disposition"),
        ("ix_classification_assessments_primary_content_label_id", "primary_content_label_id"),
    ):
        op.create_index(index_name, "classification_assessments", [column])

    op.create_table(
        "classification_assessment_labels",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "assessment_id",
            UUID,
            sa.ForeignKey("classification_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "label_id",
            UUID,
            sa.ForeignKey("taxonomy_labels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dimension", taxonomy_dimension, nullable=False),
        sa.Column("role", assessment_label_role, nullable=False, server_default="EVIDENCE"),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("source", assessment_label_source, nullable=False),
        sa.Column("evidence", sa.String(2000), nullable=False, server_default=""),
        sa.Column("provenance", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("assessment_id", "label_id", name="uq_assessment_label"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_assessment_label_confidence"),
    )
    op.create_index(
        "ix_classification_assessment_labels_assessment_id",
        "classification_assessment_labels",
        ["assessment_id"],
    )
    op.create_index(
        "uq_assessment_primary_content_label",
        "classification_assessment_labels",
        ["assessment_id"],
        unique=True,
        postgresql_where=sa.text("role = 'PRIMARY'"),
    )

    op.create_table(
        "assessment_policy_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "assessment_id",
            UUID,
            sa.ForeignKey("classification_assessments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("minimum_age", sa.Integer()),
        sa.Column("maximum_age", sa.Integer()),
        sa.Column("rating", sa.String(40)),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("block_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "policy_version_id",
            UUID,
            sa.ForeignKey("policy_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("reasons", sa.String(1000), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "minimum_age IS NULL OR minimum_age BETWEEN 0 AND 120",
            name="ck_assessment_policy_minimum_age",
        ),
        sa.CheckConstraint(
            "maximum_age IS NULL OR maximum_age BETWEEN 0 AND 120",
            name="ck_assessment_policy_maximum_age",
        ),
        sa.CheckConstraint(
            "minimum_age IS NULL OR maximum_age IS NULL OR minimum_age <= maximum_age",
            name="ck_assessment_policy_age_range",
        ),
    )
    op.create_index(
        "ix_assessment_policy_decisions_assessment_id",
        "assessment_policy_decisions",
        ["assessment_id"],
    )
    op.create_index(
        "ix_assessment_policy_decisions_policy_version_id",
        "assessment_policy_decisions",
        ["policy_version_id"],
    )

    op.execute(
        """
        CREATE FUNCTION prevent_published_taxonomy_version_mutation() RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'PUBLISHED' THEN
            RAISE EXCEPTION 'published taxonomy versions are immutable';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_taxonomy_version
          BEFORE UPDATE OR DELETE ON taxonomy_versions
          FOR EACH ROW EXECUTE FUNCTION prevent_published_taxonomy_version_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_taxonomy_label_mutation() RETURNS trigger AS $$
        DECLARE selected_version uuid;
        BEGIN
          selected_version := COALESCE(NEW.taxonomy_version_id, OLD.taxonomy_version_id);
          IF EXISTS (
            SELECT 1 FROM taxonomy_versions
            WHERE id = selected_version AND status = 'PUBLISHED'
          ) THEN
            RAISE EXCEPTION 'published taxonomy labels are immutable';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_taxonomy_labels
          BEFORE INSERT OR UPDATE OR DELETE ON taxonomy_labels
          FOR EACH ROW EXECUTE FUNCTION prevent_published_taxonomy_label_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_assessment_primary_content_label() RETURNS trigger AS $$
        DECLARE label_dimension taxonomy_dimension;
        DECLARE label_version uuid;
        BEGIN
          IF NEW.primary_content_label_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT dimension, taxonomy_version_id INTO label_dimension, label_version
            FROM taxonomy_labels WHERE id = NEW.primary_content_label_id;
          IF label_dimension <> 'CONTENT' OR label_version <> NEW.taxonomy_version_id THEN
            RAISE EXCEPTION 'assessment primary label must be content and version-matched';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER valid_assessment_primary_content_label
          BEFORE INSERT OR UPDATE ON classification_assessments
          FOR EACH ROW EXECUTE FUNCTION validate_assessment_primary_content_label()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_assessment_label_assignment() RETURNS trigger AS $$
        DECLARE label_dimension taxonomy_dimension;
        DECLARE label_version uuid;
        DECLARE assessment_version uuid;
        BEGIN
          SELECT dimension, taxonomy_version_id INTO label_dimension, label_version
            FROM taxonomy_labels WHERE id = NEW.label_id;
          SELECT taxonomy_version_id INTO assessment_version
            FROM classification_assessments WHERE id = NEW.assessment_id;
          IF label_dimension <> NEW.dimension THEN
            RAISE EXCEPTION 'assessment label dimension must match taxonomy label dimension';
          END IF;
          IF NEW.role = 'PRIMARY' AND NEW.dimension <> 'CONTENT' THEN
            RAISE EXCEPTION 'only content labels may be primary';
          END IF;
          IF label_version <> assessment_version THEN
            RAISE EXCEPTION 'assessment labels must use the assessment taxonomy version';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER valid_assessment_label_assignment
          BEFORE INSERT OR UPDATE ON classification_assessment_labels
          FOR EACH ROW EXECUTE FUNCTION validate_assessment_label_assignment()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER valid_assessment_label_assignment ON classification_assessment_labels")
    op.execute("DROP FUNCTION validate_assessment_label_assignment")
    op.execute("DROP TRIGGER valid_assessment_primary_content_label ON classification_assessments")
    op.execute("DROP FUNCTION validate_assessment_primary_content_label")
    op.execute("DROP TRIGGER immutable_published_taxonomy_labels ON taxonomy_labels")
    op.execute("DROP FUNCTION prevent_published_taxonomy_label_mutation")
    op.execute("DROP TRIGGER immutable_published_taxonomy_version ON taxonomy_versions")
    op.execute("DROP FUNCTION prevent_published_taxonomy_version_mutation")
    op.drop_index(
        "ix_assessment_policy_decisions_policy_version_id", table_name="assessment_policy_decisions"
    )
    op.drop_index(
        "ix_assessment_policy_decisions_assessment_id", table_name="assessment_policy_decisions"
    )
    op.drop_table("assessment_policy_decisions")
    op.drop_index(
        "uq_assessment_primary_content_label", table_name="classification_assessment_labels"
    )
    op.drop_index(
        "ix_classification_assessment_labels_assessment_id",
        table_name="classification_assessment_labels",
    )
    op.drop_table("classification_assessment_labels")
    for index_name in (
        "ix_classification_assessments_primary_content_label_id",
        "ix_classification_assessments_terminal_disposition",
        "ix_classification_assessments_taxonomy_version_id",
        "ix_classification_assessments_website_id",
        "ix_classification_assessments_run_id",
    ):
        op.drop_index(index_name, table_name="classification_assessments")
    op.drop_table("classification_assessments")
    op.drop_index("ix_taxonomy_labels_status", table_name="taxonomy_labels")
    op.drop_index("ix_taxonomy_labels_dimension", table_name="taxonomy_labels")
    op.drop_index("ix_taxonomy_labels_taxonomy_version_id", table_name="taxonomy_labels")
    op.drop_table("taxonomy_labels")
    op.drop_index("ix_taxonomy_versions_parent_version_id", table_name="taxonomy_versions")
    op.drop_index("ix_taxonomy_versions_status", table_name="taxonomy_versions")
    op.drop_table("taxonomy_versions")
    for enum_name in (
        "assessment_label_source",
        "assessment_label_role",
        "assessment_disposition",
        "taxonomy_dimension",
        "taxonomy_label_status",
        "taxonomy_version_status",
    ):
        postgresql.ENUM(name=enum_name).drop(op.get_bind(), checkfirst=True)
