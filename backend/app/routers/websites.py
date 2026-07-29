import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.classifier import classify_page, recommended_age_policy_name
from app.config import get_settings
from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.inspector import InspectionError, WebsiteInspector
from app.models import (
    AgePolicy,
    AuditLog,
    Category,
    ClassificationRun,
    ClassificationSource,
    RunStatus,
    Website,
    WebsiteClassification,
)
from app.normalization import NormalizationError, normalize_url
from app.schemas import (
    ClassificationExecutionResponse,
    ClassificationResponse,
    ClassificationRunResponse,
    ManualOverrideInput,
    WebsiteCheckInput,
    WebsiteCheckResponse,
    WebsiteResponse,
)

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
    website = await _website_for_input(db, payload.url)
    active = await db.scalar(
        select(ClassificationRun).where(
            ClassificationRun.website_id == website.id,
            ClassificationRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING]),
        )
    )
    if active is None:
        active = ClassificationRun(website_id=website.id, requested_by_id=user.id)
        db.add(active)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            active = await db.scalar(
                select(ClassificationRun).where(
                    ClassificationRun.website_id == website.id,
                    ClassificationRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING]),
                )
            )
            if active is None:
                raise
    else:
        await db.commit()
    await db.refresh(website)
    await db.refresh(active)
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
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    await db.commit()
    try:
        inspected = await WebsiteInspector(get_settings()).inspect(website.canonical_url)
        scores = classify_page(inspected.page)
        categories = {
            category.slug: category
            for category in (
                await db.scalars(
                    select(Category).where(Category.slug.in_([s.slug for s in scores]))
                )
            ).all()
        }
        policies = {
            policy.name: policy
            for policy in (
                await db.scalars(select(AgePolicy).where(AgePolicy.is_active.is_(True)))
            ).all()
        }
        classifications: list[WebsiteClassification] = []
        for score in scores:
            category = categories.get(score.slug)
            if category is None:
                continue
            policy_name = recommended_age_policy_name(inspected.page, score.slug)
            policy = (
                await db.get(AgePolicy, category.age_policy_id)
                if category.age_policy_id is not None
                else policies.get(policy_name)
            )
            classification = WebsiteClassification(
                website_id=website.id,
                run_id=run.id,
                category_id=category.id,
                age_policy_id=policy.id if policy else None,
                source=ClassificationSource.RULES,
                confidence=score.confidence,
                evidence=score.evidence,
                title=inspected.page.title,
                description=inspected.page.description,
                final_url=inspected.final_url,
                text_excerpt=inspected.page.visible_text[:2000],
            )
            db.add(classification)
            classifications.append(classification)
        if not classifications:
            raise InspectionError("classification_unavailable")
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(UTC)
        await db.commit()
        for item in classifications:
            await db.refresh(item)
        await db.refresh(run)
        return ClassificationExecutionResponse(
            run=ClassificationRunResponse.model_validate(run),
            classifications=[
                ClassificationResponse.model_validate(item) for item in classifications
            ],
        )
    except InspectionError as exc:
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
