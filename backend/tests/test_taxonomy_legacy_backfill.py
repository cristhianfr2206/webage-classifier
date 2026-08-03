from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    AgePolicy,
    AssessmentPolicyDecision,
    Base,
    Category,
    ClassificationAssessment,
    ClassificationAssessmentLabel,
    ClassificationRun,
    ClassificationSource,
    TaxonomyLabel,
    TaxonomyVersion,
    User,
    Website,
    WebsiteClassification,
)
from app.taxonomy import (
    INITIAL_LEGACY_TAXONOMY_VERSION,
    LEGACY_CATEGORY_LABEL_SLUGS,
    AssessmentDisposition,
    AssessmentLabelRole,
    AssessmentLabelSource,
    TaxonomyDimension,
    TaxonomyVersionStatus,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_initial_legacy_mapping_is_exact_and_limited() -> None:
    assert INITIAL_LEGACY_TAXONOMY_VERSION == "initial-legacy-v1"
    assert LEGACY_CATEGORY_LABEL_SLUGS == {
        "education": "education-reference",
        "entertainment": "entertainment-streaming",
        "social": "social-networking",
    }


async def seed_legacy_classification(
    session: AsyncSession,
) -> tuple[WebsiteClassification, TaxonomyVersion, TaxonomyLabel, AgePolicy]:
    version = TaxonomyVersion(id=uuid.uuid4(), version="legacy-test-v1", checksum="a" * 64)
    label = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.CONTENT,
        slug="education-reference",
        display_name="Education & Reference",
    )
    policy = AgePolicy(
        id=uuid.uuid4(),
        name=f"Children {uuid.uuid4()}",
        minimum_age=0,
        maximum_age=12,
        rating="children",
        review_required=True,
        blocked=False,
    )
    category = Category(
        id=uuid.uuid4(),
        name=f"Education {uuid.uuid4()}",
        slug=f"education-{uuid.uuid4()}",
        age_policy_id=policy.id,
    )
    user = User(
        id=uuid.uuid4(),
        email=f"taxonomy-{uuid.uuid4()}@example.test",
        password_hash=str(uuid.uuid4()),
    )
    website = Website(
        id=uuid.uuid4(),
        domain=f"legacy-{uuid.uuid4()}.test",
        registrable_domain="example.test",
        canonical_url="https://example.test/",
    )
    run = ClassificationRun(id=uuid.uuid4(), website_id=website.id, requested_by_id=user.id)
    classification = WebsiteClassification(
        id=uuid.uuid4(),
        website_id=website.id,
        run_id=run.id,
        category_id=category.id,
        age_policy_id=policy.id,
        source=ClassificationSource.RULES,
        confidence=88,
    )
    session.add_all([version, label, policy, category, user, website, run, classification])
    await session.flush()
    version.status = TaxonomyVersionStatus.PUBLISHED
    await session.flush()
    return classification, version, label, policy


@pytest.mark.asyncio
async def test_legacy_projection_preserves_category_and_policy_snapshot(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        classification, version, label, policy = await seed_legacy_classification(session)
        assessment = ClassificationAssessment(
            id=uuid.uuid4(),
            run_id=classification.run_id,
            website_id=classification.website_id,
            taxonomy_version_id=version.id,
            terminal_disposition=AssessmentDisposition.CLASSIFIED,
            primary_content_label_id=label.id,
        )
        association = ClassificationAssessmentLabel(
            id=uuid.uuid4(),
            assessment=assessment,
            label=label,
            dimension=TaxonomyDimension.CONTENT,
            role=AssessmentLabelRole.PRIMARY,
            confidence=classification.confidence,
            source=AssessmentLabelSource.LEGACY_BACKFILL,
            evidence=f"legacy_website_classification:{classification.id}",
            provenance="legacy-backfill",
        )
        decision = AssessmentPolicyDecision(
            id=uuid.uuid4(),
            assessment=assessment,
            minimum_age=policy.minimum_age,
            maximum_age=policy.maximum_age,
            rating=policy.rating,
            review_required=policy.review_required,
            block_recommended=policy.blocked,
            reasons=f"legacy_website_classification:{classification.id}",
        )
        session.add_all([assessment, association, decision])
        await session.commit()

        assert classification.category_id is not None
        assert classification.age_policy_id == policy.id
        assert assessment.primary_content_label_id == label.id
        assert association.source is AssessmentLabelSource.LEGACY_BACKFILL
        assert decision.minimum_age == policy.minimum_age
        assert decision.rating == policy.rating

        duplicate = ClassificationAssessment(
            id=uuid.uuid4(),
            run_id=classification.run_id,
            website_id=classification.website_id,
            taxonomy_version_id=version.id,
            terminal_disposition=AssessmentDisposition.CLASSIFIED,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_classificationless_and_infrastructure_runs_are_not_backfilled(
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
            domain=f"infrastructure-{uuid.uuid4()}.test",
            registrable_domain="example.test",
            canonical_url="https://example.test/",
        )
        infrastructure_run = ClassificationRun(
            id=uuid.uuid4(),
            website_id=website.id,
            requested_by_id=user.id,
            error_code="non_consumer_infrastructure:dns",
        )
        session.add_all([user, website, infrastructure_run])
        await session.commit()

        assessment_count = await session.scalar(select(func.count(ClassificationAssessment.id)))
        classification_count = await session.scalar(select(func.count(WebsiteClassification.id)))
        assert assessment_count == 0
        assert classification_count == 0
