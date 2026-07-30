import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_input import build_ai_payload
from app.ai_provider import AIProvider, provider_for
from app.config import Settings
from app.models import (
    AgePolicy,
    AIClassification,
    AIConfiguration,
    AIStatus,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    RunStatus,
    Website,
    WebsiteClassification,
)


class AIJobError(RuntimeError):
    pass


def should_use_ai(
    confidence: int, settings: Settings, *, conflicting: bool = False, disagreement: bool = False
) -> bool:
    return settings.ai_enabled and (
        confidence / 100 < settings.ai_confidence_threshold or conflicting or disagreement
    )


async def create_automatic_ai_fallback(
    db: AsyncSession, settings: Settings, source_run_id: uuid.UUID
) -> AIClassification | None:
    source = await db.get(ClassificationRun, source_run_id)
    if not source or source.status != RunStatus.COMPLETED or not settings.ai_enabled:
        return None
    results = list(
        (
            await db.scalars(
                select(WebsiteClassification)
                .where(WebsiteClassification.run_id == source.id)
                .order_by(WebsiteClassification.confidence.desc())
            )
        ).all()
    )
    if not results:
        return None
    confidence = results[0].confidence
    conflicting = (
        len(results) > 1
        and abs(results[0].confidence - results[1].confidence) / 100
        <= settings.ai_conflict_threshold
    )
    if not should_use_ai(confidence, settings, conflicting=conflicting):
        return None
    active = await db.scalar(
        select(AIClassification).where(
            AIClassification.website_id == source.website_id,
            AIClassification.status.in_((AIStatus.PENDING, AIStatus.RETRYING, AIStatus.RUNNING)),
        )
    )
    if active:
        return None
    task_id = str(uuid.uuid4())
    run = ClassificationRun(
        website_id=source.website_id,
        requested_by_id=source.requested_by_id,
        queue_name=QueueName.AI,
        priority=5,
        task_id=task_id,
        max_attempts=settings.ai_max_retries + 1,
    )
    db.add(run)
    await db.flush()
    job = AIClassification(
        run_id=run.id,
        website_id=source.website_id,
        trigger="conflict" if conflicting else "low_confidence",
        provider=settings.ai_provider,
        model=settings.ai_model,
        model_version=settings.ai_model_version,
        task_id=task_id,
        max_retries=settings.ai_max_retries,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _limits(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    daily = await db.scalar(
        select(func.count())
        .select_from(AIClassification)
        .where(AIClassification.created_at >= now - timedelta(days=1))
    )
    if settings.ai_daily_request_limit and int(daily or 0) >= settings.ai_daily_request_limit:
        raise AIJobError("daily_limit")
    month_cost = await db.scalar(
        select(func.coalesce(func.sum(AIClassification.estimated_cost_microunits), 0)).where(
            AIClassification.created_at >= now.replace(day=1, hour=0, minute=0, second=0)
        )
    )
    if settings.ai_monthly_cost_limit and int(month_cost or 0) >= int(
        settings.ai_monthly_cost_limit * 1_000_000
    ):
        raise AIJobError("monthly_cost_limit")


async def execute_ai_classification(
    db: AsyncSession,
    settings: Settings,
    ai_id: uuid.UUID,
    provider: AIProvider | None = None,
) -> list[WebsiteClassification]:
    runtime = await db.get(AIConfiguration, 1)
    if runtime:
        settings = settings.model_copy(
            update={
                "ai_enabled": runtime.enabled,
                "ai_provider": runtime.provider,
                "ai_model": runtime.model,
                "ai_confidence_threshold": runtime.confidence_threshold / 100,
                "ai_conflict_threshold": runtime.conflict_threshold / 100,
                "ai_screenshot_enabled": runtime.screenshot_enabled,
                "ai_max_retries": runtime.retry_limit,
                "ai_daily_request_limit": runtime.daily_request_limit,
                "ai_monthly_cost_limit": runtime.monthly_cost_limit_microunits / 1_000_000,
            }
        )
    job = await db.scalar(
        select(AIClassification).where(AIClassification.id == ai_id).with_for_update()
    )
    if job is None:
        raise AIJobError("missing")
    run = await db.get(ClassificationRun, job.run_id)
    if run is None or job.cancel_requested or run.cancel_requested:
        job.status = AIStatus.CANCELLED
        if run:
            run.status = RunStatus.CANCELLED
        await db.commit()
        raise AIJobError("cancelled")
    existing = list(
        (
            await db.scalars(
                select(WebsiteClassification).where(WebsiteClassification.run_id == run.id)
            )
        ).all()
    )
    if existing:
        return existing
    await _limits(db, settings)
    website = await db.get(Website, job.website_id)
    if website is None:
        raise AIJobError("missing_website")
    prior = list(
        (
            await db.scalars(
                select(WebsiteClassification)
                .where(
                    WebsiteClassification.website_id == website.id,
                    WebsiteClassification.run_id != run.id,
                )
                .order_by(WebsiteClassification.created_at.desc())
            )
        ).all()
    )
    if not prior:
        raise AIJobError("missing_evidence")
    evidence = prior[0]
    categories = list((await db.scalars(select(Category).order_by(Category.slug))).all())
    payload, digest, injection = build_ai_payload(
        settings,
        domain=website.registrable_domain,
        title=evidence.title,
        description=evidence.description,
        static_text=evidence.text_excerpt,
        rendered_text=evidence.text_excerpt
        if evidence.source == ClassificationSource.RENDERED
        else "",
        rule_scores={str(item.category_id): item.confidence for item in prior[:10]},
    )
    now = datetime.now(UTC)
    job.status = AIStatus.RUNNING
    job.request_started_at = now
    job.input_hash = digest
    job.input_character_count = len(json.dumps(payload))
    job.prompt_injection_suspected = injection
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or now
    await db.commit()
    result = await (provider or provider_for(settings)).classify(
        payload, [item.slug for item in categories]
    )
    output = result.output
    by_slug = {item.slug: item for item in categories}
    requested = [output.primary_category, *output.secondary_categories]
    if any(slug not in by_slug for slug in requested):
        raise AIJobError("unsupported_category")
    if output.confidence < settings.ai_confidence_threshold:
        job.status = AIStatus.REVIEW_REQUIRED
        job.manual_review_required = True
        job.validation_status = "low_confidence"
        job.confidence = round(output.confidence * 100)
        run.status = RunStatus.FAILED
        run.error_code = "manual_review_required"
        await db.commit()
        raise AIJobError("low_confidence")
    policies = []
    for slug in requested:
        category = by_slug[slug]
        policy = await db.get(AgePolicy, category.age_policy_id) if category.age_policy_id else None
        if policy is None or not policy.is_active:
            raise AIJobError("missing_age_policy")
        policies.append((category, policy))
    finished = datetime.now(UTC)
    classifications = []
    for category, policy in policies:
        item = WebsiteClassification(
            website_id=website.id,
            run_id=run.id,
            category_id=category.id,
            age_policy_id=policy.id,
            source=ClassificationSource.AI,
            confidence=round(output.confidence * 100),
            evidence=[{"source": "ai", "text": text} for text in output.evidence],
            title=evidence.title,
            description=evidence.description,
            final_url=evidence.final_url,
            text_excerpt=evidence.text_excerpt,
        )
        db.add(item)
        classifications.append(item)
    job.status = AIStatus.COMPLETED
    job.request_finished_at = finished
    job.duration_ms = int((finished - now).total_seconds() * 1000)
    job.confidence = round(output.confidence * 100)
    job.primary_category = output.primary_category
    job.secondary_categories = output.secondary_categories
    job.evidence = output.evidence
    job.intended_audience = output.intended_audience
    job.uncertainty_reason = output.uncertainty_reason
    job.validation_status = "valid"
    job.provider_request_id = result.request_id
    job.input_token_count = result.input_tokens
    job.output_token_count = result.output_tokens
    job.estimated_cost_microunits = result.estimated_cost_microunits
    usage_metadata: dict[str, object] = dict(result.usage or {})
    job.usage_metadata = usage_metadata
    job.promoted = True
    run.status = RunStatus.COMPLETED
    run.completed_at = finished
    await db.commit()
    return classifications
