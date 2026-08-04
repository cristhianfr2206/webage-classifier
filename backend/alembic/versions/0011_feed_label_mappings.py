"""Publish versioned UT1 feed mappings for offline taxonomy evaluation."""

# ruff: noqa: E501, S608

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_feed_label_mappings"
down_revision: str | None = "0010_content_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000001001"
PARENT_TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000901"
TAXONOMY_VERSION = "initial-feeds-v4"
MAPPING_VERSION = "ut1-initial-v1"
TAXONOMY_CHECKSUM = "05bae750f78025ff48b2783ac9d1da8651afa01b0168936bb807029e50b5e02d"


def upgrade() -> None:
    # PostgreSQL enum additions have to be committed before they can be used by
    # the new review-gated labels below.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taxonomy_label_status ADD VALUE IF NOT EXISTS 'INACTIVE'")

    feed_handling_mode = postgresql.ENUM(
        "FINAL_CANDIDATE",
        "SUPPORTING_EVIDENCE",
        "HIGH_RISK_EVIDENCE",
        "UNSUPPORTED",
        "IGNORE",
        name="feed_handling_mode",
        create_type=False,
    )
    feed_handling_mode.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "feed_label_mappings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "taxonomy_version_id",
            UUID,
            sa.ForeignKey("taxonomy_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_name", sa.String(80), nullable=False),
        sa.Column("source_category", sa.String(120), nullable=False),
        sa.Column(
            "target_label_id",
            UUID,
            sa.ForeignKey("taxonomy_labels.id", ondelete="RESTRICT"),
        ),
        sa.Column("handling_mode", feed_handling_mode, nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mapping_version", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_feed_label_mapping_confidence"),
        sa.UniqueConstraint(
            "taxonomy_version_id",
            "source_name",
            "source_category",
            "mapping_version",
            name="uq_feed_label_mapping_version_source_category",
        ),
    )
    op.create_index(
        "ix_feed_label_mappings_taxonomy_version_id",
        "feed_label_mappings",
        ["taxonomy_version_id"],
    )
    op.create_index("ix_feed_label_mappings_source_name", "feed_label_mappings", ["source_name"])
    op.create_index(
        "ix_feed_label_mappings_target_label_id", "feed_label_mappings", ["target_label_id"]
    )
    op.create_index(
        "ix_feed_label_mappings_handling_mode", "feed_label_mappings", ["handling_mode"]
    )
    op.create_index("ix_feed_label_mappings_enabled", "feed_label_mappings", ["enabled"])

    op.execute(
        """
        CREATE FUNCTION validate_feed_label_mapping() RETURNS trigger AS $$
        DECLARE
            target_taxonomy_version uuid;
            target_dimension taxonomy_dimension;
        BEGIN
            IF NEW.handling_mode IN ('FINAL_CANDIDATE', 'SUPPORTING_EVIDENCE', 'HIGH_RISK_EVIDENCE')
               AND NEW.target_label_id IS NULL THEN
                RAISE EXCEPTION 'feed_mapping_target_required';
            END IF;
            IF NEW.handling_mode IN ('UNSUPPORTED', 'IGNORE') AND NEW.target_label_id IS NOT NULL THEN
                RAISE EXCEPTION 'feed_mapping_target_forbidden';
            END IF;
            IF NEW.target_label_id IS NOT NULL THEN
                SELECT taxonomy_version_id, dimension
                  INTO target_taxonomy_version, target_dimension
                  FROM taxonomy_labels WHERE id = NEW.target_label_id;
                IF NOT FOUND OR target_taxonomy_version <> NEW.taxonomy_version_id THEN
                    RAISE EXCEPTION 'feed_mapping_target_taxonomy_mismatch';
                END IF;
                IF target_dimension <> 'CONTENT' THEN
                    RAISE EXCEPTION 'feed_mapping_target_must_be_content';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_published_feed_mapping_mutation() RETURNS trigger AS $$
        DECLARE
            protected_version uuid;
        BEGIN
            protected_version := CASE WHEN TG_OP = 'DELETE' THEN OLD.taxonomy_version_id ELSE NEW.taxonomy_version_id END;
            IF EXISTS (
                SELECT 1 FROM taxonomy_versions
                WHERE id = protected_version AND status = 'PUBLISHED'
            ) THEN
                RAISE EXCEPTION 'published_feed_mappings_are_immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_feed_label_mapping_target
          BEFORE INSERT OR UPDATE ON feed_label_mappings
          FOR EACH ROW EXECUTE FUNCTION validate_feed_label_mapping()
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_feed_mappings
          BEFORE INSERT OR UPDATE OR DELETE ON feed_label_mappings
          FOR EACH ROW EXECUTE FUNCTION prevent_published_feed_mapping_mutation()
        """
    )

    op.execute(
        f"""
        INSERT INTO taxonomy_versions (
            id, version, status, checksum, parent_version_id, change_notes, created_at
        ) VALUES (
            '{TAXONOMY_VERSION_ID}'::uuid,
            '{TAXONOMY_VERSION}',
            'DRAFT',
            '{TAXONOMY_CHECKSUM}',
            '{PARENT_TAXONOMY_VERSION_ID}'::uuid,
            'Versioned UT1 mappings for evaluation only; no live feed enforcement.',
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO taxonomy_labels (
            id, taxonomy_version_id, dimension, slug, display_name, definition,
            status, default_review_required, default_block_recommended, created_at
        )
        SELECT seeded.id::uuid, '{TAXONOMY_VERSION_ID}'::uuid, seeded.dimension::taxonomy_dimension,
               seeded.slug, seeded.display_name, seeded.definition, seeded.status::taxonomy_label_status,
               seeded.default_review_required, false, now()
        FROM (
            VALUES
              ('00000000-0000-4000-8000-000000001002', 'CONTENT', 'education-reference', 'Education & Reference', 'Preserved legacy content label.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001003', 'CONTENT', 'entertainment-streaming', 'Entertainment & Streaming', 'Preserved legacy content label.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001004', 'CONTENT', 'social-networking', 'Social Networking', 'Preserved legacy content label.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001005', 'SCOPE', 'analytics-advertising', 'Analytics & Advertising', 'Non-consumer analytics or advertising infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001006', 'SCOPE', 'cdn-delivery', 'CDN Delivery', 'Non-consumer content delivery infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001007', 'SCOPE', 'cloud-hosting', 'Cloud Hosting', 'Non-consumer cloud hosting infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001008', 'SCOPE', 'dns-nameserver', 'DNS & Nameserver', 'Non-consumer DNS, nameserver, or registry infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001009', 'SCOPE', 'software-update', 'Software Update', 'Non-consumer software update infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001010', 'SCOPE', 'static-asset-host', 'Static Asset Host', 'Non-consumer static asset delivery infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001011', 'SCOPE', 'time-service', 'Time Service', 'Non-consumer network time infrastructure.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001012', 'CONTENT', 'news-media', 'News & Media', 'Editorial news and media publications providing reporting or current-affairs coverage.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001013', 'CONTENT', 'shopping-ecommerce', 'Shopping & E-Commerce', 'Consumer retail and e-commerce services offering products or transactions.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001014', 'CONTENT', 'gaming', 'Gaming', 'Interactive digital games, game distribution, or game community services.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001015', 'CONTENT', 'technology-software', 'Technology & Software', 'Technology and software products, documentation, downloads, or developer services.', 'ACTIVE', false),
              ('00000000-0000-4000-8000-000000001016', 'CONTENT', 'adult-content', 'Adult Content', 'Content-risk label for adult material; inactive until review-approved taxonomy rollout.', 'INACTIVE', true),
              ('00000000-0000-4000-8000-000000001017', 'CONTENT', 'gambling', 'Gambling', 'Content-risk label for gambling material; inactive until review-approved taxonomy rollout.', 'INACTIVE', true)
        ) AS seeded(id, dimension, slug, display_name, definition, status, default_review_required)
        WHERE EXISTS (
            SELECT 1 FROM taxonomy_versions
            WHERE id = '{TAXONOMY_VERSION_ID}'::uuid AND status = 'DRAFT'
        )
        ON CONFLICT (taxonomy_version_id, slug) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO feed_label_mappings (
            id, taxonomy_version_id, source_name, source_category, target_label_id,
            handling_mode, confidence, review_required, mapping_version, enabled, created_at
        )
        SELECT seeded.id::uuid, '{TAXONOMY_VERSION_ID}'::uuid, 'ut1', seeded.source_category,
               labels.id, seeded.handling_mode::feed_handling_mode, seeded.confidence,
               seeded.review_required, '{MAPPING_VERSION}', true, now()
        FROM (
            VALUES
              ('00000000-0000-4000-8000-000000001021', 'education', 'education-reference', 'FINAL_CANDIDATE', 95, false),
              ('00000000-0000-4000-8000-000000001022', 'social_networks', 'social-networking', 'SUPPORTING_EVIDENCE', 20, false),
              ('00000000-0000-4000-8000-000000001023', 'audio-video', 'entertainment-streaming', 'SUPPORTING_EVIDENCE', 20, false),
              ('00000000-0000-4000-8000-000000001024', 'shopping', 'shopping-ecommerce', 'SUPPORTING_EVIDENCE', 20, false),
              ('00000000-0000-4000-8000-000000001025', 'games', 'gaming', 'SUPPORTING_EVIDENCE', 20, false),
              ('00000000-0000-4000-8000-000000001026', 'adult', 'adult-content', 'HIGH_RISK_EVIDENCE', 75, true),
              ('00000000-0000-4000-8000-000000001027', 'gambling', 'gambling', 'HIGH_RISK_EVIDENCE', 75, true)
        ) AS seeded(id, source_category, target_slug, handling_mode, confidence, review_required)
        JOIN taxonomy_labels AS labels
          ON labels.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
         AND labels.slug = seeded.target_slug
        WHERE EXISTS (
            SELECT 1 FROM taxonomy_versions
            WHERE id = '{TAXONOMY_VERSION_ID}'::uuid AND status = 'DRAFT'
        )
        ON CONFLICT (taxonomy_version_id, source_name, source_category, mapping_version) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE taxonomy_versions
        SET status = 'PUBLISHED', published_at = COALESCE(published_at, now())
        WHERE id = '{TAXONOMY_VERSION_ID}'::uuid AND status = 'DRAFT'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_published_feed_mappings ON feed_label_mappings")
    op.execute("DROP TRIGGER validate_feed_label_mapping_target ON feed_label_mappings")
    op.execute("DROP FUNCTION prevent_published_feed_mapping_mutation()")
    op.execute("DROP FUNCTION validate_feed_label_mapping()")
    op.drop_table("feed_label_mappings")
    op.execute("DROP TYPE feed_handling_mode")

    # Phase 1 immutability remains installed after this downgrade. Remove only
    # its triggers while deleting this migration's published v4 snapshot.
    op.execute("DROP TRIGGER immutable_published_taxonomy_labels ON taxonomy_labels")
    op.execute("DROP TRIGGER immutable_published_taxonomy_version ON taxonomy_versions")
    op.execute(
        f"""
        DELETE FROM classification_assessment_labels
        WHERE assessment_id IN (
            SELECT id FROM classification_assessments
            WHERE taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM assessment_policy_decisions
        WHERE assessment_id IN (
            SELECT id FROM classification_assessments
            WHERE taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM classification_assessments
        WHERE taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        """
    )
    op.execute(
        f"""
        DELETE FROM taxonomy_labels
        WHERE taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        """
    )
    op.execute(f"DELETE FROM taxonomy_versions WHERE id = '{TAXONOMY_VERSION_ID}'::uuid")
    op.execute(
        """
        CREATE TRIGGER immutable_published_taxonomy_version
          BEFORE UPDATE OR DELETE ON taxonomy_versions
          FOR EACH ROW EXECUTE FUNCTION prevent_published_taxonomy_version_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_published_taxonomy_labels
          BEFORE INSERT OR UPDATE OR DELETE ON taxonomy_labels
          FOR EACH ROW EXECUTE FUNCTION prevent_published_taxonomy_label_mutation()
        """
    )
