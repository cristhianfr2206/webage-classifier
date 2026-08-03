from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    ClassificationAssessment,
    ClassificationAssessmentLabel,
    ClassificationRun,
    TaxonomyLabel,
    TaxonomyVersion,
    User,
    Website,
)
from app.taxonomy import (
    AssessmentDisposition,
    AssessmentLabelRole,
    AssessmentLabelSource,
    TaxonomyDimension,
    TaxonomyInvariantError,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make_assessment_dependencies(
    session: AsyncSession, version: TaxonomyVersion
) -> ClassificationAssessment:
    user = User(
        id=uuid.uuid4(),
        email=f"taxonomy-{uuid.uuid4()}@example.test",
        password_hash=str(uuid.uuid4()),
    )
    website = Website(
        id=uuid.uuid4(),
        domain=f"taxonomy-{uuid.uuid4()}.test",
        registrable_domain="example.test",
        canonical_url="https://example.test/",
    )
    run = ClassificationRun(id=uuid.uuid4(), website_id=website.id, requested_by_id=user.id)
    assessment = ClassificationAssessment(
        id=uuid.uuid4(),
        run_id=run.id,
        website_id=website.id,
        taxonomy_version_id=version.id,
        terminal_disposition=AssessmentDisposition.UNRESOLVED,
    )
    session.add_all([user, website, run, assessment])
    await session.flush()
    return assessment


@pytest.mark.asyncio
async def test_taxonomy_labels_are_version_scoped_and_primary_is_unique(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version = TaxonomyVersion(id=uuid.uuid4(), version="draft-1", checksum="a" * 64)
        content = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="education-reference",
            display_name="Education & Reference",
        )
        secondary_content = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="science-research",
            display_name="Science & Research",
        )
        session.add_all([version, content, secondary_content])
        await session.flush()
        assessment = await make_assessment_dependencies(session, version)
        assessment.primary_content_label_id = content.id
        session.add_all(
            [
                ClassificationAssessmentLabel(
                    assessment_id=assessment.id,
                    label_id=content.id,
                    dimension=TaxonomyDimension.CONTENT,
                    role=AssessmentLabelRole.PRIMARY,
                    confidence=90,
                    source=AssessmentLabelSource.RULES,
                ),
                ClassificationAssessmentLabel(
                    assessment_id=assessment.id,
                    label_id=secondary_content.id,
                    dimension=TaxonomyDimension.CONTENT,
                    role=AssessmentLabelRole.SECONDARY,
                    confidence=80,
                    source=AssessmentLabelSource.STATIC,
                ),
            ]
        )
        await session.commit()

    async with maker() as session:
        version = await session.get(TaxonomyVersion, version.id)
        assert version is not None
        session.add(
            TaxonomyLabel(
                taxonomy_version_id=version.id,
                dimension=TaxonomyDimension.CONTENT,
                slug="education-reference",
                display_name="Duplicate",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_scope_and_security_cannot_be_primary_content(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version = TaxonomyVersion(id=uuid.uuid4(), version="draft-2", checksum="b" * 64)
        scope = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.SCOPE,
            slug="content-delivery-network",
            display_name="Content Delivery Network",
        )
        session.add_all([version, scope])
        await session.flush()
        assessment = await make_assessment_dependencies(session, version)
        session.add(
            ClassificationAssessmentLabel(
                assessment_id=assessment.id,
                label_id=scope.id,
                dimension=TaxonomyDimension.SCOPE,
                role=AssessmentLabelRole.PRIMARY,
                confidence=99,
                source=AssessmentLabelSource.INFRASTRUCTURE,
            )
        )
        with pytest.raises(TaxonomyInvariantError, match="primary_label_must_be_content"):
            await session.flush()


@pytest.mark.asyncio
async def test_assessment_persists_at_most_one_primary_content_label(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version = TaxonomyVersion(id=uuid.uuid4(), version="draft-primary", checksum="p" * 64)
        first_label = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="first-content",
            display_name="First content",
        )
        second_label = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="second-content",
            display_name="Second content",
        )
        session.add_all([version, first_label, second_label])
        await session.flush()
        assessment = await make_assessment_dependencies(session, version)
        for label in (first_label, second_label):
            session.add(
                ClassificationAssessmentLabel(
                    assessment_id=assessment.id,
                    label_id=label.id,
                    dimension=TaxonomyDimension.CONTENT,
                    role=AssessmentLabelRole.PRIMARY,
                    confidence=80,
                    source=AssessmentLabelSource.RULES,
                )
            )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_assessment_rejects_cross_version_and_dimension_mismatches(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        first = TaxonomyVersion(id=uuid.uuid4(), version="draft-3", checksum="c" * 64)
        second = TaxonomyVersion(id=uuid.uuid4(), version="draft-4", checksum="d" * 64)
        other_label = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=second,
            dimension=TaxonomyDimension.CONTENT,
            slug="business-economy",
            display_name="Business & Economy",
        )
        session.add_all([first, second, other_label])
        await session.flush()
        assessment = await make_assessment_dependencies(session, first)
        session.add(
            ClassificationAssessmentLabel(
                assessment_id=assessment.id,
                label_id=other_label.id,
                dimension=TaxonomyDimension.SECURITY,
                role=AssessmentLabelRole.EVIDENCE,
                confidence=60,
                source=AssessmentLabelSource.RULES,
            )
        )
        with pytest.raises(TaxonomyInvariantError, match="assessment_label_dimension_mismatch"):
            await session.flush()


@pytest.mark.asyncio
async def test_non_consumer_assessment_can_have_no_policy_decision(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version = TaxonomyVersion(id=uuid.uuid4(), version="draft-5", checksum="e" * 64)
        session.add(version)
        await session.flush()
        assessment = await make_assessment_dependencies(session, version)
        assessment.terminal_disposition = AssessmentDisposition.NON_CONSUMER_INFRASTRUCTURE
        await session.commit()
        assert assessment.primary_content_label_id is None
