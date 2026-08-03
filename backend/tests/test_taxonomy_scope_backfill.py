from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    AssessmentPolicyDecision,
    Base,
    ClassificationAssessment,
    ClassificationAssessmentLabel,
    ClassificationRun,
    RunStatus,
    TaxonomyLabel,
    TaxonomyVersion,
    User,
    Website,
)
from app.taxonomy import (
    INFRASTRUCTURE_SCOPE_LABEL_SLUGS,
    INITIAL_SCOPE_TAXONOMY_VERSION,
    AssessmentDisposition,
    AssessmentLabelRole,
    AssessmentLabelSource,
    TaxonomyDimension,
    TaxonomyVersionStatus,
)
from app.taxonomy_assessments import infrastructure_assessment_projection


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_scope_mapping_contains_only_currently_backfilled_detector_types() -> None:
    assert INITIAL_SCOPE_TAXONOMY_VERSION == "initial-scope-v2"
    assert INFRASTRUCTURE_SCOPE_LABEL_SLUGS == {
        "analytics_advertising": "analytics-advertising",
        "cdn_delivery": "cdn-delivery",
        "cloud_hosting": "cloud-hosting",
        "dns_nameserver": "dns-nameserver",
        "software_update": "software-update",
        "static_asset_host": "static-asset-host",
        "time_service": "time-service",
    }


def test_infrastructure_projection_is_scope_only_and_bounded() -> None:
    projection = infrastructure_assessment_projection("non_consumer_infrastructure:dns_nameserver")

    assert projection is not None
    assert projection.terminal_disposition is AssessmentDisposition.NON_CONSUMER_INFRASTRUCTURE
    assert projection.scope_label_slug == "dns-nameserver"
    assert projection.source is AssessmentLabelSource.INFRASTRUCTURE
    assert projection.provenance == "infrastructure-detector"
    assert infrastructure_assessment_projection("non_consumer_infrastructure:unknown") is None
    assert infrastructure_assessment_projection("inspection_failed") is None


async def seed_scope_taxonomy(
    session: AsyncSession,
) -> tuple[TaxonomyVersion, TaxonomyLabel, TaxonomyVersion]:
    predecessor = TaxonomyVersion(
        id=uuid.uuid4(),
        version="legacy-v1",
        checksum="a" * 64,
        status=TaxonomyVersionStatus.PUBLISHED,
    )
    successor = TaxonomyVersion(
        id=uuid.uuid4(),
        version="scope-v2",
        checksum="b" * 64,
        parent_version=predecessor,
    )
    content = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=successor,
        dimension=TaxonomyDimension.CONTENT,
        slug="education-reference",
        display_name="Education & Reference",
    )
    scope = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=successor,
        dimension=TaxonomyDimension.SCOPE,
        slug="dns-nameserver",
        display_name="DNS & Nameserver",
    )
    session.add_all([predecessor, successor, content, scope])
    await session.flush()
    successor.status = TaxonomyVersionStatus.PUBLISHED
    await session.flush()
    return successor, scope, predecessor


@pytest.mark.asyncio
async def test_infrastructure_backfill_has_scope_without_content_or_policy(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version, scope_label, predecessor = await seed_scope_taxonomy(session)
        user = User(
            id=uuid.uuid4(),
            email=f"taxonomy-{uuid.uuid4()}@example.test",
            password_hash=str(uuid.uuid4()),
        )
        website = Website(
            id=uuid.uuid4(),
            domain=f"infrastructure-{uuid.uuid4()}.test",
            registrable_domain="example.test",
            canonical_url="https://example.test/",
        )
        run = ClassificationRun(
            id=uuid.uuid4(),
            website_id=website.id,
            requested_by_id=user.id,
            status=RunStatus.COMPLETED,
            error_code="non_consumer_infrastructure:dns_nameserver",
        )
        assessment = ClassificationAssessment(
            id=uuid.uuid4(),
            run_id=run.id,
            website_id=website.id,
            taxonomy_version_id=version.id,
            terminal_disposition=AssessmentDisposition.NON_CONSUMER_INFRASTRUCTURE,
        )
        label = ClassificationAssessmentLabel(
            id=uuid.uuid4(),
            assessment=assessment,
            label=scope_label,
            dimension=TaxonomyDimension.SCOPE,
            role=AssessmentLabelRole.EVIDENCE,
            confidence=100,
            source=AssessmentLabelSource.INFRASTRUCTURE,
            evidence=run.error_code,
            provenance="legacy-infrastructure-backfill",
        )
        session.add_all([user, website, run, assessment, label])
        await session.commit()

        assert version.parent_version_id == predecessor.id
        assert assessment.primary_content_label_id is None
        assert label.dimension is TaxonomyDimension.SCOPE
        assert label.source is AssessmentLabelSource.INFRASTRUCTURE
        assert label.evidence == run.error_code
        policy_count = await session.scalar(select(func.count(AssessmentPolicyDecision.id)))
        assert policy_count == 0

        duplicate = ClassificationAssessment(
            id=uuid.uuid4(),
            run_id=run.id,
            website_id=website.id,
            taxonomy_version_id=version.id,
            terminal_disposition=AssessmentDisposition.NON_CONSUMER_INFRASTRUCTURE,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_consumer_and_failed_runs_are_not_scope_backfill_candidates(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"taxonomy-{uuid.uuid4()}@example.test",
            password_hash=str(uuid.uuid4()),
        )
        website = Website(
            id=uuid.uuid4(),
            domain=f"consumer-{uuid.uuid4()}.test",
            registrable_domain="example.test",
            canonical_url="https://example.test/",
        )
        failed_run = ClassificationRun(
            id=uuid.uuid4(),
            website_id=website.id,
            requested_by_id=user.id,
            status=RunStatus.FAILED,
            error_code="non_consumer_infrastructure:dns_nameserver",
        )
        session.add_all([user, website, failed_run])
        await session.commit()

        assert failed_run.status is RunStatus.FAILED
        assert infrastructure_assessment_projection(failed_run.error_code or "") is not None
        assessment_count = await session.scalar(select(func.count(ClassificationAssessment.id)))
        assert assessment_count == 0
