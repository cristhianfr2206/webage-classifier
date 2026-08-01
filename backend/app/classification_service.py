import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.classifier import classify_page, recommended_age_policy_name
from app.config import Settings
from app.infrastructure_detection import detect_infrastructure
from app.inspector import InspectionError, WebsiteInspector
from app.models import (
    AgePolicy,
    Category,
    ClassificationRun,
    ClassificationSource,
    ManualReviewCase,
    ReviewStatus,
    RunStatus,
    Website,
    WebsiteClassification,
)
from app.ut1_lookup import LookupResult, load_ut1_lookup, lookup_domain
from app.versioning import rules_for_classifier_version


class CancelledJob(RuntimeError):
    pass


logger = logging.getLogger(__name__)


UT1_CATEGORY_ALIASES = {
    "audio-video": "entertainment",
    "social_networks": "social",
}
UT1_UNSUPPORTED_CATEGORIES = {"shopping", "games"}
UT1_HIGH_RISK_CATEGORIES = {"adult", "gambling"}


def _offline_lookup(settings: Settings, domain: str) -> LookupResult | None:
    if not settings.ut1_fixture_path:
        return None
    try:
        lookup = load_ut1_lookup(Path(settings.ut1_fixture_path))
    except OSError:
        return None
    result = lookup_domain(domain, lookup)
    return result if result.matched else None


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

    offline = _offline_lookup(settings, website.domain)
    infrastructure = detect_infrastructure(website.domain)
    if infrastructure is not None and infrastructure.confidence_tier == "safe_exclude":
        run.status = RunStatus.COMPLETED
        run.error_code = f"non_consumer_infrastructure:{infrastructure.infrastructure_type}"
        run.completed_at = datetime.now(UTC)
        run.heartbeat_at = datetime.now(UTC)
        logger.info(
            "infrastructure_classification_excluded",
            extra={
                "domain": infrastructure.domain,
                "infrastructure_type": infrastructure.infrastructure_type,
                "confidence_tier": infrastructure.confidence_tier,
                "evidence": infrastructure.evidence,
            },
        )
        await db.commit()
        return []
    if (
        offline is not None
        and offline.match_type == "exact"
        and offline.source_category == "education"
    ):
        category_slug = "education"
        category = await db.scalar(select(Category).where(Category.slug == category_slug))
        if category is not None:
            policy = (
                await db.get(AgePolicy, category.age_policy_id) if category.age_policy_id else None
            )
            item = WebsiteClassification(
                website_id=website.id,
                run_id=run.id,
                category_id=category.id,
                age_policy_id=policy.id if policy else None,
                source=ClassificationSource.RULES,
                confidence=95,
                evidence=[
                    {
                        "source": "offline_ut1",
                        "match": "exact",
                        "category": offline.source_category,
                        "normalized_domain": offline.normalized_domain,
                    }
                ],
                title="",
                description="",
                final_url=website.canonical_url,
                text_excerpt="",
            )
            db.add(item)
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            await db.commit()
            return [item]

    inspected = await WebsiteInspector(settings).inspect(website.canonical_url)
    scores = classify_page(
        inspected.page, await rules_for_classifier_version(db, run.classifier_version_id)
    )
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
        evidence = list(score.evidence)
        if infrastructure is not None and infrastructure.confidence_tier == "evidence_only":
            evidence.append(
                {
                    "source": "infrastructure_detector",
                    "confidence_tier": infrastructure.confidence_tier,
                    "infrastructure_type": infrastructure.infrastructure_type,
                    "confidence": infrastructure.confidence,
                    "evidence": infrastructure.evidence,
                }
            )
        if offline is not None:
            feed_category = offline.source_category or ""
            offline_evidence: dict[str, object] = {
                "source": "offline_ut1",
                "match": offline.match_type,
                "category": feed_category,
                "normalized_domain": offline.normalized_domain,
                "confidence": 20,
            }
            mapped_category = UT1_CATEGORY_ALIASES.get(feed_category)
            if mapped_category is not None:
                offline_evidence["mapped_category"] = mapped_category
                offline_evidence["status"] = "preliminary"
            elif feed_category in UT1_UNSUPPORTED_CATEGORIES:
                offline_evidence["status"] = "unsupported"
            elif feed_category in UT1_HIGH_RISK_CATEGORIES:
                offline_evidence["status"] = "high_risk"
                offline_evidence["risk"] = "high"
                offline_evidence["manual_review_required"] = True
            else:
                offline_evidence["status"] = "unknown"
            evidence.append(offline_evidence)
        item = WebsiteClassification(
            website_id=website.id,
            run_id=run.id,
            category_id=category.id,
            age_policy_id=policy.id if policy else None,
            source=ClassificationSource.RULES,
            confidence=score.confidence,
            evidence=evidence,
            title=inspected.page.title,
            description=inspected.page.description,
            final_url=inspected.final_url,
            text_excerpt=inspected.page.visible_text[:2000],
        )
        db.add(item)
        classifications.append(item)
    if not classifications:
        raise InspectionError("classification_unavailable")
    if offline is not None and (offline.source_category or "") in UT1_HIGH_RISK_CATEGORIES:
        existing_review = await db.scalar(
            select(ManualReviewCase).where(ManualReviewCase.classification_run_id == run.id)
        )
        if existing_review is None:
            db.add(
                ManualReviewCase(
                    website_id=website.id,
                    classification_run_id=run.id,
                    status=ReviewStatus.PENDING,
                    reason="High-risk UT1 evidence requires HTTP confirmation and manual review",
                )
            )
    run.status = RunStatus.COMPLETED
    run.completed_at = datetime.now(UTC)
    run.heartbeat_at = datetime.now(UTC)
    await db.commit()
    return classifications
