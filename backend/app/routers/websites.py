import logging
import uuid
from asyncio import Semaphore, gather
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.classification_service import CancelledJob, execute_classification
from app.config import get_settings
from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.inspector import InspectionError
from app.models import (
    AgePolicy,
    AuditLog,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    Role,
    RunStatus,
    Website,
    WebsiteClassification,
)
from app.normalization import NormalizationError, normalize_url
from app.queueing import (
    ACTIVE_STATUSES,
    dispatch_run,
    enforce_capacity,
    enforce_enqueue_rate,
    remaining_capacity,
    revoke_run,
)
from app.schemas import (
    BulkEnqueueInput,
    BulkEnqueueResponse,
    ClassificationExecutionResponse,
    ClassificationResponse,
    ClassificationRunResponse,
    ManualOverrideInput,
    WebsiteCheckInput,
    WebsiteCheckResponse,
    WebsiteResponse,
)
from app.versioning import active_classifier_version_id

router = APIRouter(prefix="/api/websites", tags=["websites"])
logger = logging.getLogger(__name__)


async def _website_for_input(db: Db, raw_url: str) -> Website:
    try:
        target = normalize_url(raw_url)
    except NormalizationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid website URL") from exc
    website = await db.scalar(select(Website).where(Website.domain == target.host))
    if website is not None:
        return website
    website = Website(
        domain=target.host,
        registrable_domain=target.registrable_domain,
        canonical_url=target.url,
    )
    db.add(website)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(Website).where(Website.domain == target.host))
        if existing is None:
            raise
        return existing
    return website


@router.post("/check", response_model=WebsiteCheckResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_check(
    payload: WebsiteCheckInput, _: Csrf, user: CurrentUser, db: Db
) -> WebsiteCheckResponse:
    settings = get_settings()
    await enforce_enqueue_rate(settings, user.id)
    await enforce_capacity(db, settings)
    website = await _website_for_input(db, payload.url)
    should_dispatch = False
    active = await db.scalar(
        select(ClassificationRun).where(
            ClassificationRun.website_id == website.id,
            ClassificationRun.status.in_(ACTIVE_STATUSES),
        )
    )
    if active is None:
        active = ClassificationRun(
            website_id=website.id,
            requested_by_id=user.id,
            queue_name=QueueName.REALTIME,
            priority=9,
            task_id=str(uuid.uuid4()),
            max_attempts=settings.task_max_retries + 1,
            classifier_version_id=await active_classifier_version_id(db),
        )
        db.add(active)
        should_dispatch = True
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            active = await db.scalar(
                select(ClassificationRun).where(
                    ClassificationRun.website_id == website.id,
                    ClassificationRun.status.in_(ACTIVE_STATUSES),
                )
            )
            if active is None:
                raise
            should_dispatch = False
    else:
        if active.status in {RunStatus.PENDING, RunStatus.RETRYING} and (
            active.queue_name != QueueName.REALTIME or active.priority < 9
        ):
            await revoke_run(active)
            active.queue_name = QueueName.REALTIME
            active.priority = 9
            active.task_id = str(uuid.uuid4())
            should_dispatch = True
        await db.commit()
    await db.refresh(website)
    await db.refresh(active)
    try:
        if should_dispatch:
            await dispatch_run(active)
    except Exception as exc:
        logger.exception("classification_enqueue_failed", extra={"run_id": str(active.id)})
        active.status = RunStatus.FAILED
        active.error_code = "enqueue_failed"
        active.completed_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service unavailable"
        ) from exc
    return WebsiteCheckResponse(
        website=WebsiteResponse.model_validate(website),
        run=ClassificationRunResponse.model_validate(active),
    )


@router.get("/{website_id}", response_model=WebsiteResponse)
async def get_website(website_id: uuid.UUID, _: CurrentUser, db: Db) -> Website:
    website = await db.get(Website, website_id)
    if website is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Website not found")
    return website


@router.get("/{website_id}/history", response_model=list[ClassificationResponse])
async def history(website_id: uuid.UUID, _: CurrentUser, db: Db) -> list[WebsiteClassification]:
    return list(
        (
            await db.scalars(
                select(WebsiteClassification)
                .where(WebsiteClassification.website_id == website_id)
                .order_by(WebsiteClassification.created_at.desc())
                .limit(100)
            )
        ).all()
    )


@router.post(
    "/{website_id}/runs/{run_id}/classify-now",
    response_model=ClassificationExecutionResponse,
)
async def classify_now(
    website_id: uuid.UUID,
    run_id: uuid.UUID,
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> ClassificationExecutionResponse:
    run = await db.scalar(
        select(ClassificationRun).where(
            ClassificationRun.id == run_id, ClassificationRun.website_id == website_id
        )
    )
    website = await db.get(Website, website_id)
    if run is None or website is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification run not found")
    if run.status != RunStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Classification run is not pending")
    try:
        classifications = await execute_classification(db, get_settings(), run.id)
        for item in classifications:
            await db.refresh(item)
        await db.refresh(run)
        return ClassificationExecutionResponse(
            run=ClassificationRunResponse.model_validate(run),
            classifications=[
                ClassificationResponse.model_validate(item) for item in classifications
            ],
        )
    except (InspectionError, CancelledJob) as exc:
        logger.warning("website_inspection_failed", extra={"run_id": str(run.id), "code": str(exc)})
        await db.rollback()
        run.status = RunStatus.FAILED
        run.error_code = "inspection_failed"
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The website could not be safely inspected",
        ) from exc
    except Exception as exc:
        logger.exception("classification_run_failed", extra={"run_id": str(run.id)})
        await db.rollback()
        persisted_run = await db.get(ClassificationRun, run.id)
        if persisted_run is not None:
            persisted_run.status = RunStatus.FAILED
            persisted_run.error_code = "classification_failed"
            persisted_run.completed_at = datetime.now(UTC)
            await db.commit()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The classification could not be completed",
        ) from exc


@router.post(
    "/{website_id}/manual-override",
    response_model=ClassificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def manual_override(
    website_id: uuid.UUID,
    payload: ManualOverrideInput,
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> WebsiteClassification:
    website = await db.get(Website, website_id)
    category = await db.get(Category, payload.category_id)
    policy = await db.get(AgePolicy, payload.age_policy_id) if payload.age_policy_id else None
    if website is None or category is None or (payload.age_policy_id and policy is None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Website, category, or policy not found")
    classification = WebsiteClassification(
        website_id=website.id,
        category_id=category.id,
        age_policy_id=policy.id if policy else category.age_policy_id,
        source=ClassificationSource.MANUAL,
        confidence=100,
        evidence=[{"rule": "manual_override", "reason": payload.reason}],
        overridden_by_id=admin.id,
    )
    db.add(classification)
    await db.flush()
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classification.override",
            target_type="website",
            target_id=str(website.id),
            details={
                "classification_id": str(classification.id),
                "category_id": str(category.id),
                "age_policy_id": str(classification.age_policy_id)
                if classification.age_policy_id
                else None,
                "reason": payload.reason,
            },
        )
    )
    await db.commit()
    await db.refresh(classification)
    return classification


@router.get("/runs/{run_id}/status", response_model=ClassificationRunResponse)
async def run_status(run_id: uuid.UUID, user: CurrentUser, db: Db) -> ClassificationRun:
    run = await db.get(ClassificationRun, run_id)
    if run is None or (user.role != Role.ADMIN and run.requested_by_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification run not found")
    return run


@router.post("/runs/{run_id}/cancel", response_model=ClassificationRunResponse)
async def cancel_run(run_id: uuid.UUID, _: Csrf, user: CurrentUser, db: Db) -> ClassificationRun:
    run = await db.scalar(
        select(ClassificationRun).where(ClassificationRun.id == run_id).with_for_update()
    )
    if run is None or (user.role != Role.ADMIN and run.requested_by_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification run not found")
    if run.status not in ACTIVE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Classification run is not active")
    run.cancel_requested = True
    if run.status != RunStatus.RUNNING:
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="classification.cancel",
            target_type="classification_run",
            target_id=str(run.id),
            details={"status": run.status.value},
        )
    )
    await db.commit()
    await revoke_run(run)
    await db.refresh(run)
    return run


@router.post("/runs/{run_id}/retry", response_model=ClassificationRunResponse)
async def retry_run(run_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db) -> ClassificationRun:
    settings = get_settings()
    await enforce_capacity(db, settings)
    run = await db.scalar(
        select(ClassificationRun).where(ClassificationRun.id == run_id).with_for_update()
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification run not found")
    if run.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only failed or cancelled runs can be retried"
        )
    run.status = RunStatus.PENDING
    run.queue_name = QueueName.REALTIME
    run.priority = 9
    run.task_id = str(uuid.uuid4())
    run.cancel_requested = False
    run.attempts = 0
    run.error_code = None
    run.started_at = None
    run.heartbeat_at = None
    run.completed_at = None
    run.next_retry_at = None
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classification.retry",
            target_type="classification_run",
            target_id=str(run.id),
            details={"queue": run.queue_name.value, "priority": run.priority},
        )
    )
    await db.commit()
    try:
        await dispatch_run(run)
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error_code = "enqueue_failed"
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service unavailable"
        ) from exc
    await db.refresh(run)
    return run


@router.post("/bulk-enqueue", response_model=BulkEnqueueResponse)
async def bulk_enqueue(
    payload: BulkEnqueueInput, _: Csrf, admin: AdminUser, db: Db
) -> BulkEnqueueResponse:
    payload.validate_range()
    settings = get_settings()
    available = await remaining_capacity(db, settings)
    if available == 0:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Classification queue is full")
    limit = min(payload.limit, settings.bulk_enqueue_limit, available)
    websites = list(
        (
            await db.scalars(
                select(Website)
                .where(Website.tranco_rank.between(payload.rank_start, payload.rank_end))
                .order_by(Website.tranco_rank)
                .limit(limit)
            )
        ).all()
    )
    website_ids = [website.id for website in websites]
    active_ids = set(
        (
            await db.scalars(
                select(ClassificationRun.website_id).where(
                    ClassificationRun.website_id.in_(website_ids),
                    ClassificationRun.status.in_(ACTIVE_STATUSES),
                )
            )
        ).all()
    )
    runs = [
        ClassificationRun(
            website_id=website.id,
            requested_by_id=admin.id,
            queue_name=QueueName.STANDARD,
            priority=5,
            task_id=str(uuid.uuid4()),
            max_attempts=settings.task_max_retries + 1,
            classifier_version_id=await active_classifier_version_id(db),
        )
        for website in websites
        if website.id not in active_ids
    ]
    db.add_all(runs)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classification.bulk_enqueue",
            target_type="tranco_rank_range",
            target_id=f"{payload.rank_start}-{payload.rank_end}",
            details={
                "requested_limit": payload.limit,
                "effective_limit": limit,
                "created": len(runs),
                "already_active": len(active_ids),
            },
        )
    )
    await db.commit()
    semaphore = Semaphore(10)

    async def bounded_dispatch(run: ClassificationRun) -> None:
        async with semaphore:
            await dispatch_run(run)

    results = await gather(*(bounded_dispatch(run) for run in runs), return_exceptions=True)
    failed = [
        run for run, result in zip(runs, results, strict=True) if isinstance(result, Exception)
    ]
    if failed:
        now = datetime.now(UTC)
        for run in failed:
            run.status = RunStatus.FAILED
            run.error_code = "enqueue_failed"
            run.completed_at = now
        await db.commit()
    return BulkEnqueueResponse(
        created=len(runs) - len(failed),
        already_active=len(active_ids),
        enqueue_failed=len(failed),
    )
