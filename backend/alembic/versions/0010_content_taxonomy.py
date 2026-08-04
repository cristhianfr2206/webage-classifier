"""Publish the conservative Phase 4 content taxonomy snapshot."""

# ruff: noqa: S608, E501

from collections.abc import Sequence

from alembic import op

revision: str = "0010_content_taxonomy"
down_revision: str | None = "0009_scope_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000901"
PARENT_TAXONOMY_VERSION_ID = "00000000-0000-4000-8000-000000000801"
TAXONOMY_VERSION = "initial-content-v3"
# SHA-256 of the canonical ordered label tuple below.  It is deliberately
# deterministic so the published snapshot can be audited without using a live
# classifier or external feed.
TAXONOMY_CHECKSUM = "521a4d1ac13a67c124f5b3189fbdcf624022df1a01f42d19bf5ee95a90b6dcf9"


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
            'Conservative content labels for internal deterministic evaluation only.',
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    # A published snapshot cannot be changed by the Phase 1 immutability
    # trigger.  Create all labels while this successor is a draft, then publish.
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
              ('00000000-0000-4000-8000-000000000902', 'CONTENT', 'education-reference',
               'Education & Reference', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000903', 'CONTENT', 'entertainment-streaming',
               'Entertainment & Streaming', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000904', 'CONTENT', 'social-networking',
               'Social Networking', 'Preserved legacy content label.'),
              ('00000000-0000-4000-8000-000000000905', 'SCOPE', 'analytics-advertising',
               'Analytics & Advertising', 'Non-consumer analytics or advertising infrastructure.'),
              ('00000000-0000-4000-8000-000000000906', 'SCOPE', 'cdn-delivery',
               'CDN Delivery', 'Non-consumer content delivery infrastructure.'),
              ('00000000-0000-4000-8000-000000000907', 'SCOPE', 'cloud-hosting',
               'Cloud Hosting', 'Non-consumer cloud hosting infrastructure.'),
              ('00000000-0000-4000-8000-000000000908', 'SCOPE', 'dns-nameserver',
               'DNS & Nameserver', 'Non-consumer DNS, nameserver, or registry infrastructure.'),
              ('00000000-0000-4000-8000-000000000909', 'SCOPE', 'software-update',
               'Software Update', 'Non-consumer software update infrastructure.'),
              ('00000000-0000-4000-8000-000000000910', 'SCOPE', 'static-asset-host',
               'Static Asset Host', 'Non-consumer static asset delivery infrastructure.'),
              ('00000000-0000-4000-8000-000000000911', 'SCOPE', 'time-service',
               'Time Service', 'Non-consumer network time infrastructure.'),
              ('00000000-0000-4000-8000-000000000912', 'CONTENT', 'news-media',
               'News & Media', 'Editorial news and media publications providing reporting or current-affairs coverage.'),
              ('00000000-0000-4000-8000-000000000913', 'CONTENT', 'shopping-ecommerce',
               'Shopping & E-Commerce', 'Consumer retail and e-commerce services offering products or transactions.'),
              ('00000000-0000-4000-8000-000000000914', 'CONTENT', 'gaming',
               'Gaming', 'Interactive digital games, game distribution, or game community services.'),
              ('00000000-0000-4000-8000-000000000915', 'CONTENT', 'technology-software',
               'Technology & Software', 'Technology and software products, documentation, downloads, or developer services.')
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


def downgrade() -> None:
    # Keep the Phase 1 triggers and all older snapshots intact while removing
    # only the Phase 4 draft/published snapshot and any future internal rows
    # attached to it.
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
