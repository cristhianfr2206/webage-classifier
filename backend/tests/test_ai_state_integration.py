import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai_provider import AIOutput, FakeProvider
from app.ai_service import AIJobError, execute_ai_classification
from app.config import get_settings
from app.models import (
    AgePolicy,
    AIClassification,
    AIStatus,
    Base,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    Role,
    RunStatus,
    User,
    Website,
    WebsiteClassification,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(maker: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with maker() as db:
        user = User(
            email=f"{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.ADMIN,
        )
        strict = AgePolicy(
            name=f"strict-{uuid.uuid4()}",
            minimum_age=18,
            maximum_age=120,
            rating="adult",
            blocked=True,
            review_required=True,
            priority=100,
        )
        mild = AgePolicy(
            name=f"mild-{uuid.uuid4()}", minimum_age=13, maximum_age=120, rating="teen", priority=10
        )
        db.add_all([user, strict, mild])
        await db.flush()
        social = Category(name="Social", slug="social-networking", age_policy_id=mild.id)
        ugc = Category(name="UGC", slug="user-generated-content", age_policy_id=strict.id)
        site = Website(
            domain=f"{uuid.uuid4()}.example.com",
            registrable_domain="example.com",
            canonical_url="https://example.com",
        )
        db.add_all([social, ugc, site])
        await db.flush()
        prior_run = ClassificationRun(
            website_id=site.id,
            requested_by_id=user.id,
            status=RunStatus.COMPLETED,
            queue_name=QueueName.REALTIME,
            task_id=str(uuid.uuid4()),
        )
        db.add(prior_run)
        await db.flush()
        db.add(
            WebsiteClassification(
                website_id=site.id,
                run_id=prior_run.id,
                category_id=social.id,
                age_policy_id=mild.id,
                source=ClassificationSource.RULES,
                confidence=30,
                evidence=[{"rule": "ambiguous"}],
                title="Community",
                description="People post content",
                text_excerpt="profiles posts comments",
            )
        )
        run = ClassificationRun(
            website_id=site.id,
            requested_by_id=user.id,
            queue_name=QueueName.AI,
            task_id=str(uuid.uuid4()),
        )
        db.add(run)
        await db.flush()
        job = AIClassification(
            run_id=run.id,
            website_id=site.id,
            trigger="low_confidence",
            provider="fake",
            model="fake",
            task_id=run.task_id,
        )
        db.add(job)
        await db.commit()
        return job.id, site.id, strict.id


async def test_fake_provider_e2e_uses_database_policy_and_promotes_atomically(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, _, strict_policy_id = await seed(maker)
    provider = FakeProvider(
        AIOutput(
            primary_category="social-networking",
            secondary_categories=["user-generated-content"],
            confidence=0.91,
            intended_audience="teens",
            evidence=["Profiles and user posts are supplied evidence."],
            uncertainty_reason="",
            prompt_injection_suspected=False,
        )
    )
    settings = get_settings().model_copy(
        update={"ai_enabled": True, "ai_provider": "fake", "ai_model": "fake"}
    )
    async with maker() as db:
        created = await execute_ai_classification(db, settings, job_id, provider)
        assert len(created) == 2
        assert strict_policy_id in {item.age_policy_id for item in created}
        assert all(item.source == ClassificationSource.AI for item in created)
        job = await db.get(AIClassification, job_id)
        assert job and job.promoted and job.status == AIStatus.COMPLETED


@pytest.mark.parametrize(
    "output",
    [
        AIOutput(
            primary_category="invented",
            secondary_categories=[],
            confidence=0.9,
            intended_audience="",
            evidence=["e"],
            uncertainty_reason="",
            prompt_injection_suspected=False,
        ),
        AIOutput(
            primary_category="social-networking",
            secondary_categories=[],
            confidence=0.2,
            intended_audience="",
            evidence=["e"],
            uncertainty_reason="ambiguous",
            prompt_injection_suspected=False,
        ),
    ],
)
async def test_invalid_or_low_confidence_ai_preserves_previous_classification(
    maker: async_sessionmaker[AsyncSession], output: AIOutput
) -> None:
    job_id, site_id, _ = await seed(maker)
    settings = get_settings().model_copy(
        update={"ai_enabled": True, "ai_provider": "fake", "ai_model": "fake"}
    )
    async with maker() as db:
        with pytest.raises(AIJobError):
            await execute_ai_classification(db, settings, job_id, FakeProvider(output))
    async with maker() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WebsiteClassification)
                .where(WebsiteClassification.website_id == site_id)
            )
            == 1
        )
