from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import ai_tasks  # noqa: F401  # Registers isolated task definitions.
from app.ai_celery_app import ai_celery_app
from app.ai_provider import (
    AIProviderError,
    DisabledRecommendationProvider,
    FakeRecommendationProvider,
    ProviderResult,
    RecommendationOutput,
)
from app.ai_recommendation_service import (
    RecommendationError,
    cancel_recommendation,
    execute_recommendation,
    failure_class,
    mark_dispatch_result,
    reconcile_stale_recommendation_attempts,
    request_recommendation,
    reserve_attempt_dispatch,
    retry_recommendation,
)
from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import current_user
from app.main import app
from app.models import (
    AIRecommendation,
    AIRecommendationAttempt,
    AIRecommendationDispatchReservation,
    AIRecommendationUsageReservation,
    AuditLog,
    Base,
    ClassificationAssessment,
    ClassificationRun,
    ManualReviewCase,
    Role,
    TaxonomyLabel,
    TaxonomyVersion,
    User,
    Website,
    WebsiteClassification,
)
from app.review_service import claim_review_case, create_review_case
from app.taxonomy import (
    ReviewDisposition,
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


def recommendation_settings(**updates: object) -> Settings:
    return get_settings().model_copy(
        update={"ai_enabled": True, "ai_provider": "fake", "ai_model": "fake", **updates}
    )


async def seed_case(
    db: AsyncSession,
) -> tuple[User, ManualReviewCase, TaxonomyLabel, TaxonomyLabel, TaxonomyLabel]:
    actor = User(
        id=uuid.uuid4(),
        email=f"reviewer-{uuid.uuid4()}@example.test",
        password_hash=uuid.uuid4().hex,
    )
    website = Website(
        id=uuid.uuid4(),
        domain=f"review-{uuid.uuid4()}.example.test",
        registrable_domain="example.test",
        canonical_url="https://example.test/",
    )
    run = ClassificationRun(id=uuid.uuid4(), website_id=website.id, requested_by_id=actor.id)
    version = TaxonomyVersion(
        id=uuid.uuid4(), version=f"ai-review-{uuid.uuid4()}", checksum="a" * 64
    )
    content = TaxonomyLabel(
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
        display_name="Test Security",
    )
    db.add_all([actor, website, run, version, content, scope, security])
    await db.flush()
    version.status = TaxonomyVersionStatus.PUBLISHED
    case = await create_review_case(
        db,
        actor_id=actor.id,
        taxonomy_version_id=version.id,
        website_id=website.id,
        classification_run_id=run.id,
        reason="bounded stored evidence",
        evidence_payload={"title": "Example education site", "description": "Stored text only"},
    )
    await claim_review_case(db, case_id=case.id, actor_id=actor.id, expected_revision=0)
    return actor, case, content, scope, security


async def request_api(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    csrf: bool = True,
) -> httpx.Response:
    headers = {"X-CSRF-Token": "token"} if csrf else {}
    cookies = {"csrf_token": "token"} if csrf else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{get_settings().allowed_hosts[0]}"
    ) as client:
        return await client.request(method, path, json=payload, headers=headers, cookies=cookies)


@pytest.mark.asyncio
async def test_request_is_snapshot_only_idempotent_and_does_not_mutate_classification(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, content, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="same-request",
        )
        duplicate = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="same-request",
        )
        await db.flush()
        assert duplicate.id == item.id
        assert item.status == "pending"
        assert item.evidence_snapshot_id == case.evidence_snapshot_id
        assert await db.scalar(select(func.count(AIRecommendation.id))) == 1
        assert await db.scalar(select(func.count(AIRecommendationUsageReservation.id))) == 1
        assert await db.scalar(select(func.count(WebsiteClassification.id))) == 0
        assert await db.scalar(select(func.count(ClassificationAssessment.id))) == 0
        assert await db.get(TaxonomyLabel, content.id) is not None
        actions = set((await db.scalars(select(AuditLog.action))).all())
        assert {"ai_recommendation.requested", "ai_recommendation.budget_reserved"} <= actions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_change,error", [("locked", "review_case_locked"), ("cancelled", "review_case_cancelled")]
)
async def test_request_rejects_locked_or_cancelled_case(
    maker: async_sessionmaker[AsyncSession], case_change: str, error: str
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        if case_change == "locked":
            case.locked = True
            case.disposition = ReviewDisposition.RESOLVED
        else:
            case.disposition = ReviewDisposition.CANCELLED
        with pytest.raises(RecommendationError, match=error):
            await request_recommendation(
                db,
                recommendation_settings(),
                case_id=case.id,
                actor_id=actor.id,
                expected_revision=1,
                idempotency_key="rejected",
            )
        assert await db.scalar(select(func.count(AIRecommendation.id))) == 0


@pytest.mark.asyncio
async def test_request_requires_enabled_manual_only_mode_and_snapshot(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    disabled = get_settings().model_copy(update={"ai_enabled": False})
    with pytest.raises(RecommendationError, match="ai_disabled"):
        await request_recommendation(
            None,  # type: ignore[arg-type]
            disabled,
            case_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            expected_revision=0,
            idempotency_key="disabled",
        )
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        case.evidence_snapshot_id = None
        with pytest.raises(RecommendationError, match="review_case_missing_snapshot"):
            await request_recommendation(
                db,
                recommendation_settings(),
                case_id=case.id,
                actor_id=actor.id,
                expected_revision=1,
                idempotency_key="no-snapshot",
            )


def test_recommendation_output_is_strict_and_content_only_shape() -> None:
    invalid = {
        "primary_content_label": "education-reference",
        "secondary_content_labels": [],
        "confidence": 0.9,
        "evidence_references": ["snapshot.title"],
        "uncertainty_reason": "",
        "prompt_injection_suspected": False,
    }
    with pytest.raises(ValidationError):
        RecommendationOutput.model_validate({**invalid, "minimum_age": 18})
    with pytest.raises(ValidationError):
        RecommendationOutput.model_validate(
            {**invalid, "secondary_content_labels": ["education-reference"]}
        )


@pytest.mark.asyncio
async def test_execution_fake_disabled_and_invalid_output_are_controlled_and_advisory_only(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="fake",
        )
        completed = await execute_recommendation(
            db, recommendation_settings(), item.id, FakeRecommendationProvider()
        )
        assert completed.status == "completed"
        assert completed.audit_disposition == "advisory_only"
        assert await db.scalar(select(func.count(WebsiteClassification.id))) == 0
        assert await db.scalar(select(func.count(ClassificationAssessment.id))) == 0

        disabled = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="disabled-provider",
        )
        failed = await execute_recommendation(
            db, recommendation_settings(), disabled.id, DisabledRecommendationProvider()
        )
        assert failed.status == "failed"
        assert failed.failure_code == "ai_disabled"
        assert failed.failure_message == "Provider request failed"

        class InvalidProvider:
            async def recommend(
                self, snapshot: dict[str, object], allowed_labels: list[str]
            ) -> ProviderResult:
                return ProviderResult(output=object())  # type: ignore[arg-type]

        rejected = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="invalid",
        )
        invalid = await execute_recommendation(
            db, recommendation_settings(), rejected.id, InvalidProvider()
        )
        assert invalid.status == "rejected-invalid-output"
        assert invalid.failure_message == "Provider output was rejected"

        cancelled = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="cancelled",
        )
        await cancel_recommendation(db, recommendation_id=cancelled.id, actor_id=actor.id)
        assert (
            await execute_recommendation(db, recommendation_settings(), cancelled.id)
        ).status == "cancelled"


@pytest.mark.asyncio
async def test_duplicate_execution_does_not_call_provider_after_terminal_completion(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    class CountingProvider:
        calls = 0

        async def recommend(
            self, snapshot: dict[str, object], allowed_labels: list[str]
        ) -> ProviderResult:
            self.calls += 1
            return ProviderResult(
                output=RecommendationOutput(
                    primary_content_label=allowed_labels[0],
                    confidence=0.9,
                    evidence_references=["snapshot.title"],
                    uncertainty_reason="",
                    prompt_injection_suspected=False,
                )
            )

    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="duplicate-execution",
        )
        provider = CountingProvider()
        assert (
            await execute_recommendation(db, recommendation_settings(), item.id, provider)
        ).status == "completed"
        assert (
            await execute_recommendation(db, recommendation_settings(), item.id, provider)
        ).status == "completed"
        assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_class", "retryable"),
    [
        ("provider_timeout", "transient", True),
        ("provider_unavailable", "transient", True),
        ("provider_rate_limited", "transient", True),
        ("worker_lost", "transient", True),
        ("invalid_output", "permanent", False),
        ("provider_authentication", "permanent", False),
        ("unexpected_error", "permanent", False),
    ],
)
async def test_failure_classification_defaults_unknown_failures_to_permanent(
    code: str, expected_class: str, retryable: bool
) -> None:
    assert failure_class(code) == (expected_class, retryable)


@pytest.mark.asyncio
async def test_retry_creates_a_new_attempt_and_preserves_transient_failure(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    class TimeoutProvider:
        async def recommend(
            self, snapshot: dict[str, object], allowed_labels: list[str]
        ) -> ProviderResult:
            raise AIProviderError("provider_timeout", retryable=True)

    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(ai_max_retries=1),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="initial-timeout",
        )
        await execute_recommendation(db, recommendation_settings(), item.id, TimeoutProvider())
        first = await db.scalar(
            select(AIRecommendationAttempt).where(
                AIRecommendationAttempt.recommendation_id == item.id,
                AIRecommendationAttempt.attempt_number == 1,
            )
        )
        assert first is not None
        assert (first.status, first.failure_class, first.retryable) == (
            "failed-transient",
            "transient",
            True,
        )
        retry = await retry_recommendation(
            db,
            recommendation_settings(ai_max_retries=1),
            recommendation_id=item.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="retry-one",
        )
        duplicate = await retry_recommendation(
            db,
            recommendation_settings(ai_max_retries=1),
            recommendation_id=item.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="retry-one",
        )
        assert retry.id == duplicate.id
        assert retry.attempt_number == 2
        assert retry.retry_of_attempt_id == first.id
        assert (
            await db.scalar(
                select(func.count(AIRecommendationAttempt.id)).where(
                    AIRecommendationAttempt.recommendation_id == item.id
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_permanent_failure_locked_case_and_retry_limit_are_rejected(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(ai_max_retries=0),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="permanent",
        )
        await execute_recommendation(
            db, recommendation_settings(), item.id, DisabledRecommendationProvider()
        )
        with pytest.raises(RecommendationError, match="recommendation_not_retryable"):
            await retry_recommendation(
                db,
                recommendation_settings(),
                recommendation_id=item.id,
                actor_id=actor.id,
                expected_revision=1,
                idempotency_key="never",
            )
        transient = await request_recommendation(
            db,
            recommendation_settings(ai_max_retries=0),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="transient-limit",
        )

        class TimeoutProvider:
            async def recommend(
                self, snapshot: dict[str, object], allowed_labels: list[str]
            ) -> ProviderResult:
                raise AIProviderError("provider_timeout", retryable=True)

        await execute_recommendation(db, recommendation_settings(), transient.id, TimeoutProvider())
        with pytest.raises(RecommendationError, match="retry_limit_exceeded"):
            await retry_recommendation(
                db,
                recommendation_settings(ai_max_retries=0),
                recommendation_id=transient.id,
                actor_id=actor.id,
                expected_revision=1,
                idempotency_key="limit",
            )
        case.locked = True
        case.disposition = ReviewDisposition.RESOLVED
        with pytest.raises(RecommendationError, match="review_case_locked"):
            await retry_recommendation(
                db,
                recommendation_settings(),
                recommendation_id=transient.id,
                actor_id=actor.id,
                expected_revision=1,
                idempotency_key="locked",
            )


@pytest.mark.asyncio
async def test_dispatch_reservation_is_persistent_idempotent_and_recoverable(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="dispatch",
        )
        attempt = await db.scalar(
            select(AIRecommendationAttempt).where(
                AIRecommendationAttempt.recommendation_id == item.id
            )
        )
        assert attempt is not None
        reservation, dispatch_now = await reserve_attempt_dispatch(
            db, attempt_id=attempt.id, queue_name="ai"
        )
        assert dispatch_now is True
        assert reservation.state == "dispatching"
        duplicate, dispatch_again = await reserve_attempt_dispatch(
            db, attempt_id=attempt.id, queue_name="ai"
        )
        assert duplicate.id == reservation.id
        assert dispatch_again is False
        await mark_dispatch_result(db, reservation_id=reservation.id, succeeded=False)
        assert reservation.state == "failed"
        recovered, dispatch_recovered = await reserve_attempt_dispatch(
            db, attempt_id=attempt.id, queue_name="ai_realtime"
        )
        assert recovered.id == reservation.id
        assert dispatch_recovered is True
        assert recovered.queue_name == "ai_realtime"
        await mark_dispatch_result(db, reservation_id=reservation.id, succeeded=True)
        assert recovered.state == "dispatched"
        assert await db.scalar(select(func.count(AIRecommendationDispatchReservation.id))) == 1


@pytest.mark.asyncio
async def test_cancellation_prevents_attempt_completion_and_mutations(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="cancel-attempt",
        )
        attempt = await db.scalar(
            select(AIRecommendationAttempt).where(
                AIRecommendationAttempt.recommendation_id == item.id
            )
        )
        assert attempt is not None
        await cancel_recommendation(db, recommendation_id=item.id, actor_id=actor.id)
        assert attempt.status == "cancelled"
        await execute_recommendation(
            db, recommendation_settings(), item.id, FakeRecommendationProvider()
        )
        assert item.status == "cancelled"
        assert await db.scalar(select(func.count(WebsiteClassification.id))) == 0
        assert await db.scalar(select(func.count(ClassificationAssessment.id))) == 0


def test_task_is_ai_isolated_and_registered() -> None:
    assert "app.ai_tasks.execute_recommendation" in ai_celery_app.tasks
    route = ai_celery_app.conf.task_routes["app.ai_tasks.execute_recommendation"]
    assert route["queue"] == "ai"
    assert route["queue"] not in {"standard", "browser", "evaluation", "maintenance"}


@pytest.mark.asyncio
async def test_existing_api_request_list_cancel_contract_is_authenticated_and_sanitized(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with maker() as seed_db:
        actor, case, _, _, _ = await seed_case(seed_db)
        actor.role = Role.ADMIN
        await seed_db.commit()
        case_id = case.id

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    dispatched: list[uuid.UUID] = []

    async def no_dispatch(item: AIRecommendation, queue: object, **kwargs: object) -> None:
        del queue, kwargs
        dispatched.append(item.id)

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr("app.routers.ai.get_settings", recommendation_settings)
    monkeypatch.setattr("app.routers.ai.dispatch_recommendation", no_dispatch)
    try:
        path = f"/api/ai/review-cases/{case_id}/recommendations"
        anonymous = await request_api(
            "POST", path, payload={"expected_revision": 1, "idempotency_key": "anonymous"}
        )
        assert anonymous.status_code == 401, anonymous.text
        viewer = User(
            id=uuid.uuid4(),
            email=f"viewer-{uuid.uuid4()}@example.test",
            password_hash=uuid.uuid4().hex,
            role=Role.VIEWER,
        )
        app.dependency_overrides[current_user] = lambda: viewer
        assert (
            await request_api(
                "POST", path, payload={"expected_revision": 1, "idempotency_key": "viewer"}
            )
        ).status_code == 403
        app.dependency_overrides[current_user] = lambda: actor
        assert (await request_api("POST", path, payload={}, csrf=True)).status_code == 409
        assert (
            await request_api(
                "POST",
                path,
                payload={"expected_revision": 1, "idempotency_key": "api-key"},
                csrf=False,
            )
        ).status_code == 403
        created = await request_api(
            "POST", path, payload={"expected_revision": 1, "idempotency_key": "api-key"}
        )
        assert created.status_code == 202
        recommendation_id = uuid.UUID(created.json()["id"])
        duplicate = await request_api(
            "POST", path, payload={"expected_revision": 1, "idempotency_key": "api-key"}
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == created.json()["id"]
        assert dispatched == [recommendation_id]
        listed = await request_api("GET", path)
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == str(recommendation_id)
        assert "prompt" not in str(listed.json()).lower()
        assert "api_key" not in str(listed.json()).lower()

        cancelled = await request_api("POST", f"/api/ai/recommendations/{recommendation_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert (
            await request_api("POST", f"/api/ai/recommendations/{recommendation_id}/cancel")
        ).status_code == 409
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_recommendation_api_fails_closed_when_ai_disabled(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with maker() as seed_db:
        actor, case, _, _, _ = await seed_case(seed_db)
        actor.role = Role.ADMIN
        await seed_db.commit()
        case_id = case.id

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: actor
    monkeypatch.setattr("app.routers.ai.get_settings", get_settings)
    try:
        response = await request_api(
            "POST",
            f"/api/ai/review-cases/{case_id}/recommendations",
            payload={"expected_revision": 1, "idempotency_key": "disabled"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "ai_disabled"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_retry_and_detail_api_are_idempotent_and_csrf_protected(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    class TimeoutProvider:
        async def recommend(
            self, snapshot: dict[str, object], allowed_labels: list[str]
        ) -> ProviderResult:
            raise AIProviderError("provider_timeout", retryable=True)

    async with maker() as seed_db:
        actor, case, _, _, _ = await seed_case(seed_db)
        actor.role = Role.ADMIN
        item = await request_recommendation(
            seed_db,
            recommendation_settings(ai_max_retries=1),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="retry-api-initial",
        )
        await execute_recommendation(seed_db, recommendation_settings(), item.id, TimeoutProvider())
        await seed_db.commit()
        recommendation_id = item.id

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    dispatched: list[str] = []

    async def no_dispatch(item: AIRecommendation, queue: object, **kwargs: object) -> None:
        del item, queue
        dispatched.append(str(kwargs["attempt_id"]))

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: actor
    monkeypatch.setattr("app.routers.ai.get_settings", recommendation_settings)
    monkeypatch.setattr("app.routers.ai.dispatch_recommendation", no_dispatch)
    try:
        retry_path = f"/api/ai/recommendations/{recommendation_id}/retry"
        payload = {"expected_revision": 1, "idempotency_key": "retry-api"}
        assert (
            await request_api("POST", retry_path, payload=payload, csrf=False)
        ).status_code == 403
        first = await request_api("POST", retry_path, payload=payload)
        assert first.status_code == 202, first.text
        duplicate = await request_api("POST", retry_path, payload=payload)
        assert duplicate.status_code == 202
        assert duplicate.json()["attempt"] == first.json()["attempt"] == 2
        assert len(dispatched) == 1
        detail = await request_api("GET", f"/api/ai/recommendations/{recommendation_id}")
        assert detail.status_code == 200
        assert "prompt" not in str(detail.json()).lower()
        assert "api_key" not in str(detail.json()).lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reconciliation_marks_only_stale_attempts_without_provider_or_classification_work(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        stale = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="stale-pending",
        )
        fresh = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="fresh-pending",
        )
        attempts = list((await db.scalars(select(AIRecommendationAttempt))).all())
        stale_attempt = next(row for row in attempts if row.recommendation_id == stale.id)
        fresh_attempt = next(row for row in attempts if row.recommendation_id == fresh.id)
        stale_attempt.requested_at = datetime.now(UTC) - timedelta(hours=1)
        result = await reconcile_stale_recommendation_attempts(db, recommendation_settings())
        assert result["failed_transient"] == 1
        assert stale_attempt.status == "failed-transient"
        assert stale_attempt.retryable is True
        assert fresh_attempt.status == "pending"
        assert await db.scalar(select(func.count(WebsiteClassification.id))) == 0
        assert await db.scalar(select(func.count(ClassificationAssessment.id))) == 0


@pytest.mark.asyncio
async def test_reconciliation_skips_locked_and_releases_stale_reservation(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        actor, case, _, _, _ = await seed_case(db)
        item = await request_recommendation(
            db,
            recommendation_settings(),
            case_id=case.id,
            actor_id=actor.id,
            expected_revision=1,
            idempotency_key="stale-reservation",
        )
        attempt = await db.scalar(
            select(AIRecommendationAttempt).where(
                AIRecommendationAttempt.recommendation_id == item.id
            )
        )
        assert attempt is not None
        reservation, _ = await reserve_attempt_dispatch(db, attempt_id=attempt.id, queue_name="ai")
        reservation.reserved_at = datetime.now(UTC) - timedelta(hours=1)
        attempt.status = "pending"
        result = await reconcile_stale_recommendation_attempts(db, recommendation_settings())
        assert result["released_reservations"] == 1
        assert reservation.state == "failed"
        assert attempt.status == "failed-transient"
