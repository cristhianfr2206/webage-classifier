import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser_inspector import BrowserInspector, BrowserResult
from app.classifier import classify_page, recommended_age_policy_name
from app.config import Settings
from app.models import (
    AgePolicy,
    BrowserInspection,
    BrowserStatus,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    RunStatus,
    Website,
    WebsiteClassification,
)


class BrowserJobCancelled(RuntimeError):
    pass


def should_use_browser(
    static_text: str, confidence: int, settings: Settings, *, javascript_likely: bool = False
) -> bool:
    return (
        len(static_text.strip()) < settings.browser_min_static_text_characters
        or confidence < settings.browser_confidence_threshold
        or javascript_likely
    )


async def create_automatic_browser_fallback(
    db: AsyncSession, settings: Settings, source_run_id: uuid.UUID
) -> BrowserInspection | None:
    source_run = await db.get(ClassificationRun, source_run_id)
    if source_run is None or source_run.status != RunStatus.COMPLETED:
        return None
    classifications = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == source_run.id)
            )
        ).all()
    )
    if not classifications:
        return None
    confidence = max(item.confidence for item in classifications)
    static_text = max((item.text_excerpt for item in classifications), key=len, default="")
    if not should_use_browser(static_text, confidence, settings):
        return None
    active = await db.scalar(
        select(BrowserInspection)
        .join(ClassificationRun, ClassificationRun.id == BrowserInspection.run_id)
        .where(
            ClassificationRun.website_id == source_run.website_id,
            BrowserInspection.status.in_(
                (BrowserStatus.PENDING, BrowserStatus.RETRYING, BrowserStatus.RUNNING)
            ),
        )
    )
    if active is not None:
        return None
    task_id = str(uuid.uuid4())
    run = ClassificationRun(
        website_id=source_run.website_id,
        requested_by_id=source_run.requested_by_id,
        queue_name=QueueName.BROWSER,
        priority=5,
        task_id=task_id,
        max_attempts=settings.browser_max_retries + 1,
    )
    db.add(run)
    await db.flush()
    inspection = BrowserInspection(
        run_id=run.id,
        status=BrowserStatus.PENDING,
        trigger="low_confidence"
        if confidence < settings.browser_confidence_threshold
        else "static_content",
        task_id=task_id,
        max_attempts=settings.browser_max_retries + 1,
    )
    db.add(inspection)
    await db.commit()
    await db.refresh(inspection)
    return inspection


async def execute_browser_classification(
    db: AsyncSession, settings: Settings, inspection_id: uuid.UUID
) -> list[WebsiteClassification]:
    inspection = await db.scalar(
        select(BrowserInspection).where(BrowserInspection.id == inspection_id).with_for_update()
    )
    if inspection is None:
        raise BrowserJobCancelled("missing")
    run = await db.get(ClassificationRun, inspection.run_id)
    if run is None or inspection.cancel_requested or run.cancel_requested:
        inspection.status = BrowserStatus.CANCELLED
        if run is not None:
            run.status = RunStatus.CANCELLED
        await db.commit()
        raise BrowserJobCancelled("cancelled")
    existing = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == run.id)
            )
        ).all()
    )
    if existing:
        inspection.status = BrowserStatus.COMPLETED
        run.status = RunStatus.COMPLETED
        await db.commit()
        return existing
    website = await db.get(Website, run.website_id)
    if website is None:
        raise BrowserJobCancelled("missing_website")
    now = datetime.now(UTC)
    inspection.status = BrowserStatus.RUNNING
    inspection.attempts += 1
    inspection.started_at = inspection.started_at or now
    inspection.failure_code = None
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or now
    run.attempts += 1
    run.heartbeat_at = now
    await db.commit()

    result = await BrowserInspector(
        settings,
        capture_screenshot=settings.browser_screenshot_enabled
        and inspection.trigger
        in {"admin_screenshot", "low_confidence", "manual_review", "high_risk"},
    ).inspect(website.canonical_url)
    scores = classify_page(result.page)
    if not scores or any(not score.evidence for score in scores):
        raise RuntimeError("invalid_browser_evidence")

    inspection = await db.scalar(
        select(BrowserInspection).where(BrowserInspection.id == inspection_id).with_for_update()
    )
    run = await db.get(ClassificationRun, inspection.run_id) if inspection else None
    if inspection is None or run is None or inspection.cancel_requested or run.cancel_requested:
        if inspection is not None:
            inspection.status = BrowserStatus.CANCELLED
        if run is not None:
            run.status = RunStatus.CANCELLED
        await db.commit()
        raise BrowserJobCancelled("cancelled")
    existing = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == run.id)
            )
        ).all()
    )
    if existing:
        return existing
    classifications = await _validated_classifications(db, website, run, result)
    if not classifications:
        raise RuntimeError("browser_classification_unavailable")
    finished = datetime.now(UTC)
    inspection.status = BrowserStatus.COMPLETED
    inspection.finished_at = finished
    inspection.duration_ms = result.duration_ms
    inspection.rendered_final_url = result.final_url
    inspection.rendered_title = result.page.title
    inspection.rendered_text_sample = result.page.visible_text
    inspection.request_count = result.request_count
    inspection.transferred_byte_count = result.transferred_bytes
    inspection.blocked_request_count = result.blocked_requests
    inspection.artifact_id = result.artifact_id
    inspection.artifact_expires_at = (
        finished + timedelta(hours=settings.browser_artifact_retention_hours)
        if result.artifact_id
        else None
    )
    inspection.browser_version = result.browser_version
    inspection.playwright_version = result.playwright_version
    run.status = RunStatus.COMPLETED
    run.completed_at = finished
    run.heartbeat_at = finished
    await db.commit()
    return classifications


async def _validated_classifications(
    db: AsyncSession,
    website: Website,
    run: ClassificationRun,
    result: BrowserResult,
) -> list[WebsiteClassification]:
    scores = classify_page(result.page)
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
    output: list[WebsiteClassification] = []
    for score in scores:
        category = categories.get(score.slug)
        if category is None or not 0 <= score.confidence <= 100:
            continue
        policy = (
            await db.get(AgePolicy, category.age_policy_id)
            if category.age_policy_id
            else policies.get(recommended_age_policy_name(result.page, score.slug))
        )
        if policy is None:
            continue
        evidence = [dict(item, source="rendered_html") for item in score.evidence]
        item = WebsiteClassification(
            website_id=website.id,
            run_id=run.id,
            category_id=category.id,
            age_policy_id=policy.id,
            source=ClassificationSource.RENDERED,
            confidence=score.confidence,
            evidence=evidence,
            title=result.page.title,
            final_url=result.final_url,
            text_excerpt=result.page.visible_text[:2000],
        )
        db.add(item)
        output.append(item)
    return output
