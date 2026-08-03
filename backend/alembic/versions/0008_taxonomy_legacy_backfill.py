"""Publish the initial legacy taxonomy and backfill legacy classifications."""

# ruff: noqa: S608

from collections.abc import Sequence

from alembic import op

revision: str = "0008_taxonomy_legacy_backfill"
down_revision: str | None = "0007_taxonomy_dimensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000701"
TAXONOMY_VERSION = "initial-legacy-v1"
TAXONOMY_CHECKSUM = "266b2f7dc453ae9532698e4f71d7ce71f5afbc3c6b1ee7edb835af1983d63838"


def upgrade() -> None:
    # PostgreSQL requires a committed enum change before the new value is used.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE assessment_label_source ADD VALUE IF NOT EXISTS 'LEGACY_BACKFILL'")

    op.execute(
        f"""
        INSERT INTO taxonomy_versions (
            id, version, status, checksum, change_notes, created_at
        ) VALUES (
            '{TAXONOMY_VERSION_ID}'::uuid,
            '{TAXONOMY_VERSION}',
            'DRAFT',
            '{TAXONOMY_CHECKSUM}',
            'Initial immutable mapping of legacy categories only.',
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
        SELECT seeded.id::uuid, '{TAXONOMY_VERSION_ID}'::uuid, 'CONTENT', seeded.slug,
               seeded.display_name, seeded.definition, 'ACTIVE', false, false, now()
        FROM (
            VALUES
              ('00000000-0000-4000-8000-000000000702', 'education-reference',
               'Education & Reference', 'Legacy education category mapping.'),
              ('00000000-0000-4000-8000-000000000703', 'entertainment-streaming',
               'Entertainment & Streaming', 'Legacy entertainment category mapping.'),
              ('00000000-0000-4000-8000-000000000704', 'social-networking',
               'Social Networking', 'Legacy social category mapping.')
        ) AS seeded(id, slug, display_name, definition)
        WHERE EXISTS (
            SELECT 1 FROM taxonomy_versions
            WHERE id = '{TAXONOMY_VERSION_ID}'::uuid AND status = 'DRAFT'
        )
        ON CONFLICT (taxonomy_version_id, slug) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE taxonomy_versions
        SET status = 'PUBLISHED', published_at = COALESCE(published_at, now())
        WHERE id = '{TAXONOMY_VERSION_ID}'::uuid AND status = 'DRAFT'
        """
    )

    op.execute(
        f"""
        WITH category_mapping(legacy_slug, taxonomy_slug) AS (
            VALUES
              ('education', 'education-reference'),
              ('entertainment', 'entertainment-streaming'),
              ('social', 'social-networking')
        ),
        candidates AS (
            SELECT
                wc.id AS classification_id,
                wc.run_id,
                wc.website_id,
                labels.id AS label_id,
                wc.confidence,
                wc.created_at,
                row_number() OVER (
                    PARTITION BY wc.run_id
                    ORDER BY wc.created_at DESC, wc.id DESC
                ) AS primary_rank
            FROM website_classifications AS wc
            JOIN classification_runs AS runs ON runs.id = wc.run_id AND runs.status = 'COMPLETED'
            JOIN categories AS categories ON categories.id = wc.category_id
            JOIN category_mapping AS mapping ON mapping.legacy_slug = categories.slug
            JOIN taxonomy_labels AS labels
              ON labels.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
             AND labels.slug = mapping.taxonomy_slug
            WHERE wc.run_id IS NOT NULL
        ),
        primary_records AS (
            SELECT * FROM candidates WHERE primary_rank = 1
        ),
        deterministic_ids AS (
            SELECT
                primary_records.*,
                md5('taxonomy-assessment:' || primary_records.run_id::text) AS digest
            FROM primary_records
        )
        INSERT INTO classification_assessments (
            id, run_id, website_id, taxonomy_version_id, terminal_disposition,
            primary_content_label_id, created_at, updated_at
        )
        SELECT
            (
                substr(digest, 1, 8) || '-' || substr(digest, 9, 4) || '-' ||
                substr(digest, 13, 4) || '-' || substr(digest, 17, 4) || '-' ||
                substr(digest, 21, 12)
            )::uuid,
            run_id,
            website_id,
            '{TAXONOMY_VERSION_ID}'::uuid,
            'CLASSIFIED',
            label_id,
            created_at,
            created_at
        FROM deterministic_ids
        ON CONFLICT (run_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        WITH category_mapping(legacy_slug, taxonomy_slug) AS (
            VALUES
              ('education', 'education-reference'),
              ('entertainment', 'entertainment-streaming'),
              ('social', 'social-networking')
        ),
        candidates AS (
            SELECT
                wc.id AS classification_id,
                wc.run_id,
                labels.id AS label_id,
                wc.confidence,
                wc.created_at,
                md5('taxonomy-assessment-label:' || wc.id::text) AS digest,
                row_number() OVER (
                    PARTITION BY wc.run_id
                    ORDER BY wc.created_at DESC, wc.id DESC
                ) AS primary_rank,
                row_number() OVER (
                    PARTITION BY wc.run_id, labels.id
                    ORDER BY wc.created_at DESC, wc.id DESC
                ) AS label_rank
            FROM website_classifications AS wc
            JOIN classification_runs AS runs ON runs.id = wc.run_id AND runs.status = 'COMPLETED'
            JOIN categories AS categories ON categories.id = wc.category_id
            JOIN category_mapping AS mapping ON mapping.legacy_slug = categories.slug
            JOIN taxonomy_labels AS labels
              ON labels.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
             AND labels.slug = mapping.taxonomy_slug
            WHERE wc.run_id IS NOT NULL
        )
        INSERT INTO classification_assessment_labels (
            id, assessment_id, label_id, dimension, role, confidence, source,
            evidence, provenance, created_at
        )
        SELECT
            (
                substr(candidates.digest, 1, 8) || '-' ||
                substr(candidates.digest, 9, 4) || '-' ||
                substr(candidates.digest, 13, 4) || '-' ||
                substr(candidates.digest, 17, 4) || '-' ||
                substr(candidates.digest, 21, 12)
            )::uuid,
            assessments.id,
            candidates.label_id,
            'CONTENT'::taxonomy_dimension,
            (
                CASE WHEN candidates.primary_rank = 1 THEN 'PRIMARY' ELSE 'SECONDARY' END
            )::assessment_label_role,
            candidates.confidence,
            'LEGACY_BACKFILL'::assessment_label_source,
            'legacy_website_classification:' || candidates.classification_id::text,
            'legacy-backfill',
            candidates.created_at
        FROM candidates
        JOIN classification_assessments AS assessments
          ON assessments.run_id = candidates.run_id
         AND assessments.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        WHERE candidates.label_rank = 1
        ON CONFLICT (assessment_id, label_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        WITH category_mapping(legacy_slug, taxonomy_slug) AS (
            VALUES
              ('education', 'education-reference'),
              ('entertainment', 'entertainment-streaming'),
              ('social', 'social-networking')
        ),
        primary_records AS (
            SELECT
                wc.id AS classification_id,
                wc.run_id,
                wc.age_policy_id,
                wc.created_at,
                runs.classifier_version_id,
                md5('taxonomy-policy-decision:' || wc.run_id::text) AS digest,
                row_number() OVER (
                    PARTITION BY wc.run_id
                    ORDER BY wc.created_at DESC, wc.id DESC
                ) AS primary_rank
            FROM website_classifications AS wc
            JOIN classification_runs AS runs ON runs.id = wc.run_id AND runs.status = 'COMPLETED'
            JOIN categories AS categories ON categories.id = wc.category_id
            JOIN category_mapping AS mapping ON mapping.legacy_slug = categories.slug
            WHERE wc.run_id IS NOT NULL
        )
        INSERT INTO assessment_policy_decisions (
            id, assessment_id, minimum_age, maximum_age, rating, review_required,
            block_recommended, policy_version_id, reasons, created_at
        )
        SELECT
            (
                substr(primary_records.digest, 1, 8) || '-' ||
                substr(primary_records.digest, 9, 4) || '-' ||
                substr(primary_records.digest, 13, 4) || '-' ||
                substr(primary_records.digest, 17, 4) || '-' ||
                substr(primary_records.digest, 21, 12)
            )::uuid,
            assessments.id,
            policies.minimum_age,
            policies.maximum_age,
            policies.rating,
            policies.review_required,
            policies.blocked,
            classifier_versions.policy_version_id,
            'legacy_website_classification:' || primary_records.classification_id::text,
            primary_records.created_at
        FROM primary_records
        JOIN classification_assessments AS assessments
          ON assessments.run_id = primary_records.run_id
         AND assessments.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        LEFT JOIN age_policies AS policies ON policies.id = primary_records.age_policy_id
        LEFT JOIN classifier_versions
          ON classifier_versions.id = primary_records.classifier_version_id
        WHERE primary_records.primary_rank = 1
        ON CONFLICT (assessment_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Phase 1 immutability remains installed after this downgrade, so temporarily
    # remove only its triggers while deleting this migration's published snapshot.
    op.execute("DROP TRIGGER immutable_published_taxonomy_labels ON taxonomy_labels")
    op.execute("DROP TRIGGER immutable_published_taxonomy_version ON taxonomy_versions")
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
        DELETE FROM classification_assessment_labels
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

    op.execute("ALTER TABLE classification_assessment_labels ALTER COLUMN source DROP DEFAULT")
    op.execute("ALTER TYPE assessment_label_source RENAME TO assessment_label_source_phase2")
    op.execute(
        """
        CREATE TYPE assessment_label_source AS ENUM (
            'RULES', 'STATIC', 'RENDERED', 'SCREENSHOT', 'UT1', 'INFRASTRUCTURE', 'AI', 'MANUAL'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE classification_assessment_labels
        ALTER COLUMN source TYPE assessment_label_source
        USING source::text::assessment_label_source
        """
    )
    op.execute("DROP TYPE assessment_label_source_phase2")
