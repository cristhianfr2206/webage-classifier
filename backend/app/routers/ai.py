import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.ai_queueing import dispatch_ai, dispatch_recommendation, revoke_ai
from app.ai_recommendation_service import (
    RecommendationError,
    cancel_recommendation,
    mark_dispatch_result,
    request_recommendation,
    reserve_attempt_dispatch,
    retry_recommendation,
)
from app.config import get_settings
from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.models import (
    AIClassification,
    AIConfiguration,
    AIRecommendation,
    AIRecommendationAttempt,
    AIStatus,
    AuditLog,
    ClassificationRun,
    QueueName,
    Role,
    RunStatus,
    Website,
)
from app.queueing import enforce_ai_enqueue_rate
from app.schemas import (
    AIClassificationResponse,
    AIRequest,
    AIRequestResponse,
    AISettingsResponse,
    AISettingsUpdate,
    AIUsageResponse,
    ClassificationRunResponse,
)
from app.versioning import active_classifier_version_id

router = APIRouter(prefix="/api/ai", tags=["ai"])
ACTIVE = (AIStatus.PENDING, AIStatus.RETRYING, AIStatus.RUNNING)


def _review_request_payload(payload: dict[str, object]) -> tuple[int, str]:
    revision_value = payload["expected_revision"]
    if not isinstance(revision_value, int | str):
        raise ValueError("expected_revision must be an integer")
    idempotency_key = str(payload["idempotency_key"]).strip()[:120]
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    return int(revision_value), idempotency_key


@router.post("/review-cases/{case_id}/recommendations", status_code=status.HTTP_202_ACCEPTED)
async def request_case_recommendation(
    case_id: uuid.UUID,
    payload: dict[str, object],
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> dict[str, object]:
    try:
        expected_revision, idempotency_key = _review_request_payload(payload)
        item = await request_recommendation(
            db,
            get_settings(),
            case_id=case_id,
            actor_id=admin.id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        attempt = await db.scalar(
            select(AIRecommendationAttempt)
            .where(AIRecommendationAttempt.recommendation_id == item.id)
            .order_by(AIRecommendationAttempt.attempt_number.desc())
        )
        if attempt is None:
            raise RecommendationError("attempt_missing")
        dispatch, should_dispatch = await reserve_attempt_dispatch(
            db, attempt_id=attempt.id, queue_name=QueueName.AI_REALTIME.value
        )
        await db.commit()
        if should_dispatch:
            try:
                await dispatch_recommendation(
                    item,
                    QueueName.AI_REALTIME,
                    task_id=dispatch.task_id,
                    attempt_id=str(attempt.id),
                )
            except Exception as exc:
                await mark_dispatch_result(db, reservation_id=dispatch.id, succeeded=False)
                await db.commit()
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "AI queue unavailable"
                ) from exc
            await mark_dispatch_result(db, reservation_id=dispatch.id, succeeded=True)
            await db.commit()
    except (KeyError, ValueError, RecommendationError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)[:120]) from exc
    return {"id": item.id, "status": item.status, "confidence": item.confidence}


@router.get("/review-cases/{case_id}/recommendations")
async def list_case_recommendations(
    case_id: uuid.UUID, _: AdminUser, db: Db
) -> list[dict[str, object]]:
    rows = list(
        (
            await db.scalars(
                select(AIRecommendation).where(AIRecommendation.review_case_id == case_id)
            )
        ).all()
    )
    return [
        {
            "id": row.id,
            "status": row.status,
            "confidence": row.confidence,
            "result": row.result,
            "failure_code": row.failure_code,
        }
        for row in rows
    ]


@router.get("/recommendations/{recommendation_id}")
async def recommendation_detail(
    recommendation_id: uuid.UUID, _: AdminUser, db: Db
) -> dict[str, object]:
    row = await db.get(AIRecommendation, recommendation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recommendation not found")
    return {
        "id": row.id,
        "status": row.status,
        "confidence": row.confidence,
        "result": row.result,
        "failure_code": row.failure_code,
    }


@router.get("/maintenance/recommendations/status")
async def recommendation_maintenance_status(_: AdminUser, db: Db) -> dict[str, int]:
    now = datetime.now(UTC)
    rows = list(
        (
            await db.scalars(
                select(AIRecommendationAttempt).where(
                    AIRecommendationAttempt.status.in_(("pending", "dispatched", "running"))
                )
            )
        ).all()
    )
    return {
        "pending": sum(row.status == "pending" for row in rows),
        "dispatched": sum(row.status == "dispatched" for row in rows),
        "running": sum(row.status == "running" for row in rows),
        "active_leases": sum(
            row.maintenance_lease_expires_at is not None and row.maintenance_lease_expires_at > now
            for row in rows
        ),
    }


@router.post("/recommendations/{recommendation_id}/cancel")
async def cancel_case_recommendation(
    recommendation_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    try:
        item = await cancel_recommendation(
            db, recommendation_id=recommendation_id, actor_id=admin.id
        )
        await db.commit()
    except RecommendationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)[:120]) from exc
    return {"id": item.id, "status": item.status}


@router.post("/recommendations/{recommendation_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_case_recommendation(
    recommendation_id: uuid.UUID, payload: dict[str, object], _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    try:
        expected_revision, idempotency_key = _review_request_payload(payload)
        attempt = await retry_recommendation(
            db,
            get_settings(),
            recommendation_id=recommendation_id,
            actor_id=admin.id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        dispatch, should_dispatch = await reserve_attempt_dispatch(
            db, attempt_id=attempt.id, queue_name=QueueName.AI_REALTIME.value
        )
        item = await db.get(AIRecommendation, recommendation_id)
        await db.commit()
        if item is not None and should_dispatch:
            try:
                await dispatch_recommendation(
                    item,
                    QueueName.AI_REALTIME,
                    task_id=dispatch.task_id,
                    attempt_id=str(attempt.id),
                )
            except Exception as exc:
                await mark_dispatch_result(db, reservation_id=dispatch.id, succeeded=False)
                await db.commit()
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "AI queue unavailable"
                ) from exc
            await mark_dispatch_result(db, reservation_id=dispatch.id, succeeded=True)
            await db.commit()
    except (KeyError, ValueError, RecommendationError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)[:120]) from exc
    return {
        "recommendation_id": recommendation_id,
        "attempt": attempt.attempt_number,
        "status": attempt.status,
    }


async def _authorized(ai_id: uuid.UUID, user: CurrentUser, db: Db) -> AIClassification:
    job = await db.get(AIClassification, ai_id)
    run = await db.get(ClassificationRun, job.run_id) if job else None
    if not job or not run or (user.role != Role.ADMIN and run.requested_by_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI classification not found")
    return job


@router.post(
    "/websites/{website_id}/classify",
    response_model=AIRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_ai(
    website_id: uuid.UUID, payload: AIRequest, _: Csrf, admin: AdminUser, db: Db
) -> AIRequestResponse:
    settings = get_settings()
    runtime = await db.get(AIConfiguration, 1)
    if runtime:
        settings = settings.model_copy(
            update={
                "ai_enabled": runtime.enabled,
                "ai_provider": runtime.provider,
                "ai_model": runtime.model,
                "ai_max_retries": runtime.retry_limit,
                "ai_confidence_threshold": runtime.confidence_threshold / 100,
                "ai_conflict_threshold": runtime.conflict_threshold / 100,
                "ai_screenshot_enabled": runtime.screenshot_enabled,
                "ai_daily_request_limit": runtime.daily_request_limit,
                "ai_monthly_cost_limit": runtime.monthly_cost_limit_microunits / 1_000_000,
            }
        )
    if not settings.ai_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI classification is disabled")
    website = await db.get(Website, website_id)
    if not website:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Website not found")
    await enforce_ai_enqueue_rate(settings, admin.id, website.registrable_domain)
    active = await db.scalar(
        select(AIClassification).where(
            AIClassification.website_id == website_id, AIClassification.status.in_(ACTIVE)
        )
    )
    if active:
        run = await db.get(ClassificationRun, active.run_id)
        return AIRequestResponse(
            run=ClassificationRunResponse.model_validate(run),
            ai=AIClassificationResponse.model_validate(active),
        )
    task_id = str(uuid.uuid4())
    run = ClassificationRun(
        website_id=website.id,
        requested_by_id=admin.id,
        queue_name=QueueName.AI_REALTIME,
        priority=9,
        task_id=task_id,
        max_attempts=settings.ai_max_retries + 1,
        classifier_version_id=await active_classifier_version_id(db),
    )
    db.add(run)
    await db.flush()
    job = AIClassification(
        run_id=run.id,
        website_id=website.id,
        trigger=payload.trigger,
        provider=settings.ai_provider,
        model=settings.ai_model,
        model_version=settings.ai_model_version,
        task_id=task_id,
        max_retries=settings.ai_max_retries,
    )
    db.add(job)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="ai.request",
            target_type="website",
            target_id=str(website.id),
            details={"trigger": payload.trigger},
        )
    )
    await db.commit()
    try:
        await dispatch_ai(job, QueueName.AI_REALTIME)
    except Exception as exc:
        job.status = AIStatus.FAILED
        job.failure_code = "enqueue_failed"
        job.failure_message = "AI queue unavailable"
        run.status = RunStatus.FAILED
        run.error_code = "enqueue_failed"
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI queue unavailable") from exc
    return AIRequestResponse(
        run=ClassificationRunResponse.model_validate(run),
        ai=AIClassificationResponse.model_validate(job),
    )


@router.get("/runs/{ai_id}", response_model=AIClassificationResponse)
async def ai_status(ai_id: uuid.UUID, user: CurrentUser, db: Db) -> AIClassification:
    return await _authorized(ai_id, user, db)


@router.post("/runs/{ai_id}/cancel", response_model=AIClassificationResponse)
async def cancel_ai(ai_id: uuid.UUID, _: Csrf, user: CurrentUser, db: Db) -> AIClassification:
    job = await _authorized(ai_id, user, db)
    if job.status not in ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI classification is not active")
    run = await db.get(ClassificationRun, job.run_id)
    job.cancel_requested = True
    if run:
        run.cancel_requested = True
    if job.status != AIStatus.RUNNING:
        job.status = AIStatus.CANCELLED
        if run:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now(UTC)
    await db.commit()
    await revoke_ai(job)
    return job


@router.post("/runs/{ai_id}/retry", response_model=AIClassificationResponse)
async def retry_ai(ai_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db) -> AIClassification:
    job = await _authorized(ai_id, admin, db)
    if job.status not in {AIStatus.FAILED, AIStatus.REVIEW_REQUIRED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "AI classification cannot be retried")
    run = await db.get(ClassificationRun, job.run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI classification not found")
    task_id = str(uuid.uuid4())
    job.status = AIStatus.PENDING
    job.failure_code = None
    job.failure_message = None
    job.cancel_requested = False
    job.manual_review_required = False
    job.task_id = task_id
    run.status = RunStatus.PENDING
    run.cancel_requested = False
    run.task_id = task_id
    await db.commit()
    try:
        await dispatch_ai(job, QueueName.AI_REALTIME)
    except Exception as exc:
        job.status = AIStatus.FAILED
        job.failure_code = "enqueue_failed"
        run.status = RunStatus.FAILED
        await db.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AI queue unavailable") from exc
    return job


@router.post(
    "/websites/{website_id}/recommendation",
    response_model=AIRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_review_recommendation(
    website_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> AIRequestResponse:
    return await request_ai(website_id, AIRequest(trigger="manual_review"), None, admin, db)


@router.get("/settings", response_model=AISettingsResponse)
async def ai_settings(_: AdminUser, db: Db) -> AISettingsResponse:
    settings = get_settings()
    row = await db.get(AIConfiguration, 1)
    if row:
        return AISettingsResponse(
            enabled=row.enabled,
            provider=row.provider,
            model=row.model,
            confidence_threshold=row.confidence_threshold / 100,
            conflict_threshold=row.conflict_threshold / 100,
            screenshot_enabled=row.screenshot_enabled,
            retry_limit=row.retry_limit,
            daily_request_limit=row.daily_request_limit,
            monthly_cost_limit=row.monthly_cost_limit_microunits / 1_000_000,
        )
    return AISettingsResponse(
        enabled=settings.ai_enabled,
        provider=settings.ai_provider,
        model=settings.ai_model,
        confidence_threshold=settings.ai_confidence_threshold,
        conflict_threshold=settings.ai_conflict_threshold,
        screenshot_enabled=settings.ai_screenshot_enabled,
        retry_limit=settings.ai_max_retries,
        daily_request_limit=settings.ai_daily_request_limit,
        monthly_cost_limit=settings.ai_monthly_cost_limit,
    )


@router.put("/settings", response_model=AISettingsResponse)
async def update_ai_settings(
    payload: AISettingsUpdate, _: Csrf, admin: AdminUser, db: Db
) -> AISettingsResponse:
    environment = get_settings()
    if (
        payload.enabled
        and payload.provider != "fake"
        and (not environment.ai_api_key or not environment.ai_endpoint)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Configured provider credentials are unavailable",
        )
    row = await db.get(AIConfiguration, 1)
    if row is None:
        row = AIConfiguration(id=1)
        db.add(row)
    row.enabled = payload.enabled
    row.provider = payload.provider
    row.model = payload.model
    row.confidence_threshold = round(payload.confidence_threshold * 100)
    row.conflict_threshold = round(payload.conflict_threshold * 100)
    row.screenshot_enabled = payload.screenshot_enabled
    row.retry_limit = payload.retry_limit
    row.daily_request_limit = payload.daily_request_limit
    row.monthly_cost_limit_microunits = round(payload.monthly_cost_limit * 1_000_000)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="ai.settings.update",
            target_type="ai_configuration",
            target_id="1",
            details={
                "enabled": payload.enabled,
                "provider": payload.provider,
                "model": payload.model,
            },
        )
    )
    await db.commit()
    return AISettingsResponse(
        enabled=row.enabled,
        provider=row.provider,
        model=row.model,
        confidence_threshold=row.confidence_threshold / 100,
        conflict_threshold=row.conflict_threshold / 100,
        screenshot_enabled=row.screenshot_enabled,
        retry_limit=row.retry_limit,
        daily_request_limit=row.daily_request_limit,
        monthly_cost_limit=row.monthly_cost_limit_microunits / 1_000_000,
    )


@router.get("/usage", response_model=AIUsageResponse)
async def ai_usage(_: AdminUser, db: Db) -> AIUsageResponse:
    since = datetime.now(UTC) - timedelta(days=30)
    count, cost = (
        await db.execute(
            select(
                func.count(AIClassification.id),
                func.coalesce(func.sum(AIClassification.estimated_cost_microunits), 0),
            ).where(AIClassification.created_at >= since)
        )
    ).one()
    return AIUsageResponse(requests=int(count), estimated_cost_microunits=int(cost))
