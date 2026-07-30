import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.artifacts import ArtifactError, ArtifactStore
from app.browser_queueing import dispatch_browser, revoke_browser
from app.config import get_settings
from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.models import (
    AuditLog,
    BrowserInspection,
    BrowserStatus,
    ClassificationRun,
    QueueName,
    Role,
    RunStatus,
    Website,
)
from app.queueing import ACTIVE_STATUSES, enforce_browser_enqueue_rate, enforce_capacity
from app.schemas import (
    BrowserInspectionRequest,
    BrowserInspectionResponse,
    BrowserRequestResponse,
    ClassificationRunResponse,
)
from app.versioning import active_classifier_version_id

router = APIRouter(prefix="/api/browser", tags=["browser"])
ACTIVE_BROWSER = (BrowserStatus.PENDING, BrowserStatus.RETRYING, BrowserStatus.RUNNING)


async def _authorized(
    inspection_id: uuid.UUID, user: CurrentUser, db: Db
) -> tuple[BrowserInspection, ClassificationRun]:
    inspection = await db.get(BrowserInspection, inspection_id)
    run = await db.get(ClassificationRun, inspection.run_id) if inspection else None
    if (
        inspection is None
        or run is None
        or (user.role != Role.ADMIN and run.requested_by_id != user.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Browser inspection not found")
    return inspection, run


@router.post(
    "/websites/{website_id}/reinspect",
    response_model=BrowserRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_browser_inspection(
    website_id: uuid.UUID,
    payload: BrowserInspectionRequest,
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> BrowserRequestResponse:
    settings = get_settings()
    await enforce_browser_enqueue_rate(settings, admin.id)
    await enforce_capacity(db, settings)
    website = await db.get(Website, website_id)
    if website is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Website not found")
    active_run = await db.scalar(
        select(ClassificationRun).where(
            ClassificationRun.website_id == website_id,
            ClassificationRun.status.in_(ACTIVE_STATUSES),
        )
    )
    if active_run is not None:
        active_browser = await db.scalar(
            select(BrowserInspection).where(BrowserInspection.run_id == active_run.id)
        )
        if active_browser is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "A classification run is already active")
    active = await db.scalar(
        select(BrowserInspection)
        .join(ClassificationRun, ClassificationRun.id == BrowserInspection.run_id)
        .where(
            ClassificationRun.website_id == website_id,
            BrowserInspection.status.in_(ACTIVE_BROWSER),
        )
    )
    if active is not None:
        run = await db.get(ClassificationRun, active.run_id)
        if run is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Browser inspection is already active")
        return BrowserRequestResponse(
            run=ClassificationRunResponse.model_validate(run),
            browser=BrowserInspectionResponse.model_validate(active),
        )
    task_id = str(uuid.uuid4())
    run = ClassificationRun(
        website_id=website.id,
        requested_by_id=admin.id,
        queue_name=QueueName.BROWSER_REALTIME,
        priority=9,
        task_id=task_id,
        max_attempts=settings.browser_max_retries + 1,
        classifier_version_id=await active_classifier_version_id(db),
    )
    db.add(run)
    await db.flush()
    inspection = BrowserInspection(
        run_id=run.id,
        status=BrowserStatus.PENDING,
        trigger="admin_screenshot" if payload.capture_screenshot else "admin",
        task_id=task_id,
        max_attempts=settings.browser_max_retries + 1,
    )
    db.add(inspection)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="browser.request",
            target_type="website",
            target_id=str(website.id),
            details={"capture_screenshot": payload.capture_screenshot},
        )
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(inspection)
    try:
        await dispatch_browser(inspection, QueueName.BROWSER_REALTIME)
    except Exception as exc:
        inspection.status = BrowserStatus.FAILED
        inspection.failure_code = "enqueue_failed"
        run.status = RunStatus.FAILED
        run.error_code = "enqueue_failed"
        await db.commit()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Browser queue unavailable"
        ) from exc
    return BrowserRequestResponse(
        run=ClassificationRunResponse.model_validate(run),
        browser=BrowserInspectionResponse.model_validate(inspection),
    )


@router.get("/runs/{inspection_id}", response_model=BrowserInspectionResponse)
async def browser_status(inspection_id: uuid.UUID, user: CurrentUser, db: Db) -> BrowserInspection:
    inspection, _ = await _authorized(inspection_id, user, db)
    return inspection


@router.post("/runs/{inspection_id}/cancel", response_model=BrowserInspectionResponse)
async def cancel_browser(
    inspection_id: uuid.UUID, _: Csrf, user: CurrentUser, db: Db
) -> BrowserInspection:
    inspection, run = await _authorized(inspection_id, user, db)
    if inspection.status not in ACTIVE_BROWSER:
        raise HTTPException(status.HTTP_409_CONFLICT, "Browser inspection is not active")
    inspection.cancel_requested = True
    run.cancel_requested = True
    if inspection.status != BrowserStatus.RUNNING:
        inspection.status = BrowserStatus.CANCELLED
        inspection.finished_at = datetime.now(UTC)
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="browser.cancel",
            target_type="browser_inspection",
            target_id=str(inspection.id),
            details={},
        )
    )
    await db.commit()
    await revoke_browser(inspection)
    await db.refresh(inspection)
    return inspection


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, user: CurrentUser, db: Db) -> FileResponse:
    inspection = await db.scalar(
        select(BrowserInspection).where(BrowserInspection.artifact_id == artifact_id)
    )
    if inspection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    _, run = await _authorized(inspection.id, user, db)
    del run
    expires_at = inspection.artifact_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is None or expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    try:
        path = ArtifactStore(
            get_settings().browser_artifact_root,
            get_settings().browser_screenshot_max_bytes,
        ).path_for_read(artifact_id)
    except ArtifactError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found") from exc
    return FileResponse(path, media_type="image/png", filename=f"{artifact_id}.png")


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(artifact_id: str, _: Csrf, admin: AdminUser, db: Db) -> None:
    inspection = await db.scalar(
        select(BrowserInspection).where(BrowserInspection.artifact_id == artifact_id)
    )
    if inspection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    try:
        ArtifactStore(
            get_settings().browser_artifact_root,
            get_settings().browser_screenshot_max_bytes,
        ).delete(artifact_id)
    except ArtifactError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found") from exc
    inspection.artifact_id = None
    inspection.artifact_expires_at = None
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="browser.artifact.delete",
            target_type="browser_inspection",
            target_id=str(inspection.id),
            details={"artifact_id": artifact_id},
        )
    )
    await db.commit()
