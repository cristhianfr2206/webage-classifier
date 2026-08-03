"""Publish scope taxonomy successor and backfill infrastructure exclusions."""

# ruff: noqa: S608

from collections.abc import Sequence

from alembic import op

revision: str = "0009_scope_backfill"
down_revision: str | None = "0008_taxonomy_legacy_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000801"
PARENT_TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000701"
TAXONOMY_VERSION = "initial-scope-v2"
TAXONOMY_CHECKSUM = "89b9ab89436bcc53cefde9247843760f0cc95b11bd83c942d0f9d03c96574248"


def upgrade() -> None:
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
            'Scope-only successor for existing non-consumer infrastructure outcomes.',
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
               seeded.slug, seeded.display_name, seeded.definition, 'ACTIVE', false, false, now()
        FROM (
            VALUES
              ('00000000-0000-4000-8000-000000000802', 'CONTENT', 'education-reference',
               'Education & Reference', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000803', 'CONTENT', 'entertainment-streaming',
               'Entertainment & Streaming', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000804', 'CONTENT', 'social-networking',
               'Social Networking', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000805', 'SCOPE', 'analytics-advertising',
               'Analytics & Advertising', 'Non-consumer analytics or advertising infrastructure.'),
              ('00000000-0000-4000-8000-000000000806', 'SCOPE', 'cdn-delivery',
               'CDN Delivery', 'Non-consumer content delivery infrastructure.'),
              ('00000000-0000-4000-8000-000000000807', 'SCOPE', 'cloud-hosting',
               'Cloud Hosting', 'Non-consumer cloud hosting infrastructure.'),
              ('00000000-0000-4000-8000-000000000808', 'SCOPE', 'dns-nameserver',
               'DNS & Nameserver', 'Non-consumer DNS, nameserver, or registry infrastructure.'),
              ('00000000-0000-4000-8000-000000000809', 'SCOPE', 'software-update',
               'Software Update', 'Non-consumer software update infrastructure.'),
              ('00000000-0000-4000-8000-000000000810', 'SCOPE', 'static-asset-host',
               'Static Asset Host', 'Non-consumer static asset delivery infrastructure.'),
              ('00000000-0000-4000-8000-000000000811', 'SCOPE', 'time-service',
               'Time Service', 'Non-consumer network time infrastructure.')
        ) AS seeded(id, dimension, slug, display_name, definition)
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
        WITH scope_mapping(infrastructure_type, scope_slug) AS (
            VALUES
              ('analytics_advertising', 'analytics-advertising'),
              ('cdn_delivery', 'cdn-delivery'),
              ('cloud_hosting', 'cloud-hosting'),
              ('dns_nameserver', 'dns-nameserver'),
              ('software_update', 'software-update'),
              ('static_asset_host', 'static-asset-host'),
              ('time_service', 'time-service')
        ),
        candidates AS (
            SELECT
                runs.id AS run_id,
                runs.website_id,
                runs.error_code,
                coalesce(runs.completed_at, runs.created_at) AS recorded_at,
                labels.id AS label_id,
                md5('taxonomy-infrastructure-assessment:' || runs.id::text) AS digest
            FROM classification_runs AS runs
            LEFT JOIN website_classifications AS classifications ON classifications.run_id = runs.id
            JOIN scope_mapping
              ON scope_mapping.infrastructure_type = split_part(runs.error_code, ':', 2)
            JOIN taxonomy_labels AS labels
              ON labels.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
             AND labels.slug = scope_mapping.scope_slug
            WHERE runs.status = 'COMPLETED'
              AND runs.error_code LIKE 'non_consumer_infrastructure:%'
              AND classifications.id IS NULL
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
            'NON_CONSUMER_INFRASTRUCTURE',
            NULL,
            recorded_at,
            recorded_at
        FROM candidates
        ON CONFLICT (run_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        WITH scope_mapping(infrastructure_type, scope_slug) AS (
            VALUES
              ('analytics_advertising', 'analytics-advertising'),
              ('cdn_delivery', 'cdn-delivery'),
              ('cloud_hosting', 'cloud-hosting'),
              ('dns_nameserver', 'dns-nameserver'),
              ('software_update', 'software-update'),
              ('static_asset_host', 'static-asset-host'),
              ('time_service', 'time-service')
        ),
        candidates AS (
            SELECT
                runs.id AS run_id,
                runs.error_code,
                coalesce(runs.completed_at, runs.created_at) AS recorded_at,
                labels.id AS label_id,
                md5('taxonomy-infrastructure-label:' || runs.id::text) AS digest
            FROM classification_runs AS runs
            LEFT JOIN website_classifications AS classifications ON classifications.run_id = runs.id
            JOIN scope_mapping
              ON scope_mapping.infrastructure_type = split_part(runs.error_code, ':', 2)
            JOIN taxonomy_labels AS labels
              ON labels.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
             AND labels.slug = scope_mapping.scope_slug
            WHERE runs.status = 'COMPLETED'
              AND runs.error_code LIKE 'non_consumer_infrastructure:%'
              AND classifications.id IS NULL
        )
        INSERT INTO classification_assessment_labels (
            id, assessment_id, label_id, dimension, role, confidence, source,
            evidence, provenance, created_at
        )
        SELECT
            (
                substr(digest, 1, 8) || '-' || substr(digest, 9, 4) || '-' ||
                substr(digest, 13, 4) || '-' || substr(digest, 17, 4) || '-' ||
                substr(digest, 21, 12)
            )::uuid,
            assessments.id,
            candidates.label_id,
            'SCOPE'::taxonomy_dimension,
            'EVIDENCE'::assessment_label_role,
            100,
            'INFRASTRUCTURE'::assessment_label_source,
            candidates.error_code,
            'legacy-infrastructure-backfill',
            candidates.recorded_at
        FROM candidates
        JOIN classification_assessments AS assessments
          ON assessments.run_id = candidates.run_id
         AND assessments.taxonomy_version_id = '{TAXONOMY_VERSION_ID}'::uuid
        ON CONFLICT (assessment_id, label_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Phase 1 immutability remains installed after this downgrade, so temporarily
    # remove only its triggers while deleting this migration's published snapshot.
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
