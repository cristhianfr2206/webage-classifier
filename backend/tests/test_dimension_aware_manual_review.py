from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    AgePolicy,
    AIConfiguration,
    AuditLog,
    Base,
    Category,
    ClassificationRun,
    ManualReviewCase,
    ManualReviewEvidenceSnapshot,
    ManualReviewLabelDecision,
    TaxonomyLabel,
    TaxonomyVersion,
    User,
    Website,
    WebsiteClassification,
)
from app.review_service import (
    ReviewConflictError,
    ReviewLockedError,
    ReviewValidationError,
    claim_review_case,
    create_review_case,
    lock_review_case,
    release_review_case,
    reopen_review_case,
    resolve_review_case,
    submit_label_decision,
)
from app.taxonomy import (
    ReviewDecisionState,
    ReviewDisposition,
    ReviewLabelRole,
    TaxonomyDimension,
    TaxonomyInvariantError,
    TaxonomyVersionStatus,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_review_dependencies(
    session: AsyncSession,
) -> tuple[User, TaxonomyVersion, TaxonomyLabel, TaxonomyLabel, TaxonomyLabel, ClassificationRun]:
    actor = User(
        id=uuid.uuid4(),
        email=f"reviewer-{uuid.uuid4()}@example.test",
        password_hash=str(uuid.uuid4()),
    )
    website = Website(
        id=uuid.uuid4(),
        domain=f"review-{uuid.uuid4()}.test",
        registrable_domain="example.test",
        canonical_url="https://example.test/",
    )
    run = ClassificationRun(id=uuid.uuid4(), website_id=website.id, requested_by_id=actor.id)
    version = TaxonomyVersion(
        id=uuid.uuid4(), version=f"review-v-{uuid.uuid4()}", checksum="a" * 64
    )
    education = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.CONTENT,
        slug="education-reference",
        display_name="Education & Reference",
    )
    scope = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.SCOPE,
        slug="dns-nameserver",
        display_name="DNS & Nameserver",
    )
    security = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.SECURITY,
        slug="test-security",
        display_name="Test security",
    )
    policy = AgePolicy(
        id=uuid.uuid4(), name=f"Children-{uuid.uuid4()}", minimum_age=0, maximum_age=12
    )
    category = Category(
        id=uuid.uuid4(), name=f"Education-{uuid.uuid4()}", slug="education", age_policy_id=policy.id
    )
    session.add_all([actor, website, run, version, education, scope, security, policy, category])
    await session.flush()
    version.status = TaxonomyVersionStatus.PUBLISHED
    await session.flush()
    return actor, version, education, scope, security, run


async def create_case(
    session: AsyncSession,
    actor: User,
    version: TaxonomyVersion,
    run: ClassificationRun,
) -> ManualReviewCase:
    return await create_review_case(
        session,
        actor_id=actor.id,
        taxonomy_version_id=version.id,
        website_id=run.website_id,
        classification_run_id=run.id,
        reason="Stored evidence requires an explicit human decision",
        evidence_payload={
            "title": "Example education resource",
            "description": "Stored plain-text description",
            "rule_evidence": ["education"],
        },
    )


@pytest.mark.asyncio
async def test_case_creation_captures_one_immutable_stored_evidence_snapshot(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, _, _, _, run = await seed_review_dependencies(session)
        case = await create_case(session, actor, version, run)
        await session.commit()
        snapshot = await session.get(ManualReviewEvidenceSnapshot, case.evidence_snapshot_id)
        assert snapshot is not None
        assert snapshot.review_case_id == case.id
        assert snapshot.source_classification_run_id == run.id
        assert case.revision == 0
        actions = list(
            (
                await session.scalars(
                    select(AuditLog.action).where(AuditLog.target_id == str(case.id))
                )
            ).all()
        )
        assert actions == ["manual_review.case_created", "manual_review.snapshot_captured"]
        snapshot.provenance = "tamper"
        with pytest.raises(TaxonomyInvariantError, match="review_evidence_snapshot_is_immutable"):
            await session.flush()


@pytest.mark.asyncio
async def test_evidence_rejects_secrets_internal_values_and_unbounded_payload(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, _, _, _, run = await seed_review_dependencies(session)
        with pytest.raises(ReviewValidationError, match="forbidden_key"):
            await create_review_case(
                session,
                actor_id=actor.id,
                taxonomy_version_id=version.id,
                reason="test",
                classification_run_id=run.id,
                evidence_payload={"api_key": "nope"},
            )
        with pytest.raises(ReviewValidationError, match="forbidden_value"):
            await create_review_case(
                session,
                actor_id=actor.id,
                taxonomy_version_id=version.id,
                reason="test",
                classification_run_id=run.id,
                evidence_payload={"text": "http://127.0.0.1/private"},
            )
        with pytest.raises(ReviewValidationError, match="too_large"):
            await create_review_case(
                session,
                actor_id=actor.id,
                taxonomy_version_id=version.id,
                reason="test",
                classification_run_id=run.id,
                evidence_payload={"text": "x" * 70_000},
            )


@pytest.mark.asyncio
async def test_claim_release_are_revision_safe_and_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, _, _, _, run = await seed_review_dependencies(session)
        other = User(
            id=uuid.uuid4(),
            email=f"other-{uuid.uuid4()}@example.test",
            password_hash=str(uuid.uuid4()),
        )
        session.add(other)
        case = await create_case(session, actor, version, run)
        claimed = await claim_review_case(
            session, case_id=case.id, actor_id=actor.id, expected_revision=0
        )
        assert claimed.revision == 1
        assert claimed.disposition is ReviewDisposition.IN_REVIEW
        same = await claim_review_case(
            session, case_id=case.id, actor_id=actor.id, expected_revision=1
        )
        assert same.revision == 1
        with pytest.raises(ReviewConflictError, match="already_claimed"):
            await claim_review_case(
                session, case_id=case.id, actor_id=other.id, expected_revision=1
            )
        with pytest.raises(ReviewConflictError, match="revision_conflict"):
            await release_review_case(
                session, case_id=case.id, actor_id=actor.id, expected_revision=0
            )
        released = await release_review_case(
            session, case_id=case.id, actor_id=actor.id, expected_revision=1, reason="handoff"
        )
        assert released.revision == 2
        assert released.disposition is ReviewDisposition.PENDING_REVIEW
        assert (
            await release_review_case(
                session, case_id=case.id, actor_id=actor.id, expected_revision=2
            )
        ).revision == 2


@pytest.mark.asyncio
async def test_primary_decision_is_active_content_version_scoped_and_policy_is_derived(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, education, scope, security, run = await seed_review_dependencies(session)
        case = await create_case(session, actor, version, run)
        await claim_review_case(session, case_id=case.id, actor_id=actor.id, expected_revision=0)
        with pytest.raises(ReviewValidationError, match="primary_must_be_active_content"):
            await submit_label_decision(
                session,
                case_id=case.id,
                actor_id=actor.id,
                expected_revision=1,
                taxonomy_label_id=scope.id,
                dimension=TaxonomyDimension.SCOPE,
                role=ReviewLabelRole.PRIMARY,
                state=ReviewDecisionState.ACCEPTED,
                reviewer_confidence=90,
                rationale="Not content",
            )
        with pytest.raises(ReviewValidationError, match="dimension_mismatch"):
            await submit_label_decision(
                session,
                case_id=case.id,
                actor_id=actor.id,
                expected_revision=1,
                taxonomy_label_id=security.id,
                dimension=TaxonomyDimension.CONTENT,
                role=ReviewLabelRole.SUPPORTING,
                state=ReviewDecisionState.REJECTED,
                reviewer_confidence=20,
                rationale="Wrong dimension",
            )
        first = await submit_label_decision(
            session,
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            taxonomy_label_id=education.id,
            dimension=TaxonomyDimension.CONTENT,
            role=ReviewLabelRole.PRIMARY,
            state=ReviewDecisionState.ACCEPTED,
            reviewer_confidence=96,
            rationale="Strong stored evidence",
        )
        assert case.final_category_id is not None
        assert case.final_age_policy_id is not None
        assert first.superseded_at is None
        await session.commit()
        assert await session.scalar(select(func.count(WebsiteClassification.id))) == 0


@pytest.mark.asyncio
async def test_unresolved_lock_and_privileged_reopen_are_atomic_and_audited(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, _, _, _, run = await seed_review_dependencies(session)
        case = await create_case(session, actor, version, run)
        await claim_review_case(session, case_id=case.id, actor_id=actor.id, expected_revision=0)
        resolved = await resolve_review_case(
            session,
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            disposition=ReviewDisposition.UNRESOLVED,
            reason="Evidence is insufficient",
        )
        assert resolved.final_age_policy_id is None
        locked = await lock_review_case(
            session, case_id=case.id, actor_id=actor.id, expected_revision=2, reason="final review"
        )
        assert locked.locked and locked.revision == 3
        with pytest.raises(ReviewLockedError):
            await claim_review_case(
                session, case_id=case.id, actor_id=actor.id, expected_revision=3
            )
        with pytest.raises(ReviewValidationError, match="privileged_reopen_required"):
            await reopen_review_case(
                session,
                case_id=case.id,
                actor_id=actor.id,
                expected_revision=3,
                reason="",
                privileged=False,
            )
        reopened = await reopen_review_case(
            session,
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=3,
            reason="New stored evidence was audited",
            privileged=True,
        )
        await session.commit()
        assert not reopened.locked
        assert reopened.disposition is ReviewDisposition.PENDING_REVIEW
        actions = set((await session.scalars(select(AuditLog.action))).all())
        assert {
            "manual_review.resolved",
            "manual_review.locked",
            "manual_review.reopened",
        } <= actions


@pytest.mark.asyncio
async def test_legacy_review_rows_remain_compatible_and_review_creation_dispatches_nothing(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, _, _, _, run = await seed_review_dependencies(session)
        legacy = ManualReviewCase(
            website_id=run.website_id,
            classification_run_id=run.id,
            reason="legacy review",  # No fabricated taxonomy or snapshot.
        )
        session.add(legacy)
        case = await create_case(session, actor, version, run)
        session.add(AIConfiguration(id=1, enabled=False, provider="disabled"))
        await session.commit()
        assert legacy.taxonomy_version_id is None
        assert legacy.evidence_snapshot_id is None
        assert case.evidence_snapshot_id is not None
        ai_configuration = await session.get(AIConfiguration, 1)
        assert ai_configuration is not None
        assert ai_configuration.enabled is False
        assert await session.scalar(select(func.count(WebsiteClassification.id))) == 0


@pytest.mark.asyncio
async def test_database_primary_constraint_rejects_two_active_primary_rows(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        actor, version, education, _, _, run = await seed_review_dependencies(session)
        case = await create_case(session, actor, version, run)
        await claim_review_case(session, case_id=case.id, actor_id=actor.id, expected_revision=0)
        for _ in range(2):
            session.add(
                ManualReviewLabelDecision(
                    review_case_id=case.id,
                    taxonomy_label_id=education.id,
                    taxonomy_version_id=version.id,
                    dimension=TaxonomyDimension.CONTENT,
                    role=ReviewLabelRole.PRIMARY,
                    state=ReviewDecisionState.ACCEPTED,
                    reviewer_confidence=80,
                    rationale="test",
                    reviewer_id=actor.id,
                )
            )
        with pytest.raises(IntegrityError):
            await session.flush()
