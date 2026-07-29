import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.classifier import classify_page, recommended_age_policy_name
from app.config import Settings
from app.inspector import InspectionError, WebsiteInspector
from app.models import (
    AgePolicy,
    Category,
    ClassificationRun,
    ClassificationSource,
    RunStatus,
    Website,
    WebsiteClassification,
)


class CancelledJob(RuntimeError):
    pass


async def execute_classification(
    db: AsyncSession, settings: Settings, run_id: uuid.UUID
) -> list[WebsiteClassification]:
    run = await db.scalar(
        select(ClassificationRun).where(ClassificationRun.id == run_id).with_for_update()
    )
    if run is None:
        raise CancelledJob("missing")
    existing = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == run.id)
            )
        ).all()
    )
    if existing:
        if run.status != RunStatus.COMPLETED:
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(UTC)
            await db.commit()
        return existing
    if run.cancel_requested or run.status == RunStatus.CANCELLED:
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
        await db.commit()
        raise CancelledJob("cancelled")
    if run.status not in {RunStatus.PENDING, RunStatus.RETRYING, RunStatus.RUNNING}:
        raise CancelledJob("terminal")
    website = await db.get(Website, run.website_id)
    if website is None:
        raise CancelledJob("missing_website")
    run.status = RunStatus.RUNNING
    run.attempts += 1
    run.started_at = run.started_at or datetime.now(UTC)
    run.heartbeat_at = datetime.now(UTC)
    run.error_code = None
    await db.commit()

    inspected = await WebsiteInspector(settings).inspect(website.canonical_url)
    scores = classify_page(inspected.page)
    if not scores:
        raise InspectionError("classification_unavailable")

    run = await db.scalar(
        select(ClassificationRun).where(ClassificationRun.id == run_id).with_for_update()
    )
    if run is None or run.cancel_requested or run.status == RunStatus.CANCELLED:
        if run is not None:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now(UTC)
            await db.commit()
        raise CancelledJob("cancelled")
    existing = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == run.id)
            )
        ).all()
    )
    if existing:
        run.status = RunStatus.COMPLETED
        run.completed_at = datetime.now(UTC)
        await db.commit()
        return existing
    categories = {
        item.slug: item
        for item in (
            await db.scalars(
                select(Category).where(Category.slug.in_([score.slug for score in scores]))
            )
        ).all()
    }
    policies = {
        item.name: item
        for item in (await db.scalars(select(AgePolicy).where(AgePolicy.is_active.is_(True)))).all()
    }
    classifications: list[WebsiteClassification] = []
    for score in scores:
        category = categories.get(score.slug)
        if category is None:
            continue
        policy = (
            await db.get(AgePolicy, category.age_policy_id)
            if category.age_policy_id
            else policies.get(recommended_age_policy_name(inspected.page, score.slug))
        )
        item = WebsiteClassification(
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
        db.add(item)
        classifications.append(item)
    if not classifications:
        raise InspectionError("classification_unavailable")
    run.status = RunStatus.COMPLETED
    run.completed_at = datetime.now(UTC)
    run.heartbeat_at = datetime.now(UTC)
    await db.commit()
    return classifications
