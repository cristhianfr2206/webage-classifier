"""Recommendation-only AI work for explicitly claimed, unlocked review cases."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_provider import (
    RecommendationOutput,
    RecommendationProvider,
    recommendation_provider_for,
)
from app.config import Settings
from app.models import (
    AIRecommendation,
    AIRecommendationUsageReservation,
    AuditLog,
    ManualReviewCase,
    ManualReviewEvidenceSnapshot,
    TaxonomyLabel,
)
from app.taxonomy import TaxonomyDimension, TaxonomyLabelStatus

PROMPT_VERSION = "manual-review-content-v1"
PROMPT_CHECKSUM = hashlib.sha256(PROMPT_VERSION.encode()).hexdigest()

TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "rejected-invalid-output",
    "rejected-case-locked",
    "rejected-budget",
    "rejected-rate-limit",
}


class RecommendationError(ValueError):
    pass


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _audit(
    db: AsyncSession, actor: uuid.UUID, item: AIRecommendation, action: str, reason: str = ""
) -> None:
    db.add(
        AuditLog(
            actor_id=actor,
            action=action,
            target_type="ai_recommendation",
            target_id=str(item.id),
            details={
                "review_case_id": str(item.review_case_id),
                "snapshot_id": str(item.evidence_snapshot_id),
                "taxonomy_version_id": str(item.taxonomy_version_id),
                "status": item.status,
                "reason": reason[:300],
            },
        )
    )


async def request_recommendation(
    db: AsyncSession,
    settings: Settings,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    idempotency_key: str,
) -> AIRecommendation:
    if not settings.ai_enabled or settings.ai_recommendation_mode != "manual_only":
        raise RecommendationError("ai_disabled")
    case = await db.scalar(
        select(ManualReviewCase).where(ManualReviewCase.id == case_id).with_for_update()
    )
    if not case or not case.taxonomy_version_id or not case.evidence_snapshot_id:
        raise RecommendationError("review_case_missing_snapshot")
    if case.locked:
        raise RecommendationError("review_case_locked")
    if case.disposition and case.disposition.value == "cancelled":
        raise RecommendationError("review_case_cancelled")
    if case.revision != expected_revision:
        raise RecommendationError("review_revision_conflict")
    if case.claimed_by_id != actor_id:
        raise RecommendationError("review_case_must_be_claimed_by_reviewer")
    snapshot = await db.get(ManualReviewEvidenceSnapshot, case.evidence_snapshot_id)
    if snapshot is None:
        raise RecommendationError("review_case_missing_snapshot")
    labels = list(
        (
            await db.scalars(
                select(TaxonomyLabel)
                .where(
                    TaxonomyLabel.taxonomy_version_id == case.taxonomy_version_id,
                    TaxonomyLabel.dimension == TaxonomyDimension.CONTENT,
                    TaxonomyLabel.status == TaxonomyLabelStatus.ACTIVE,
                )
                .order_by(TaxonomyLabel.slug)
            )
        ).all()
    )
    if not labels:
        raise RecommendationError("no_active_content_labels")
    allowed = [item.slug for item in labels]
    allowed_hash = _hash(allowed)
    existing = await db.scalar(
        select(AIRecommendation).where(
            AIRecommendation.review_case_id == case.id,
            AIRecommendation.evidence_snapshot_id == snapshot.id,
            AIRecommendation.taxonomy_version_id == case.taxonomy_version_id,
            AIRecommendation.provider == settings.ai_provider,
            AIRecommendation.model == settings.ai_model,
            AIRecommendation.prompt_version == PROMPT_VERSION,
            AIRecommendation.allowed_label_checksum == allowed_hash,
            AIRecommendation.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    daily = await db.scalar(
        select(func.count(AIRecommendationUsageReservation.id)).where(
            AIRecommendationUsageReservation.reviewer_id == actor_id,
            AIRecommendationUsageReservation.created_at
            >= datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    if settings.ai_daily_request_limit and int(daily or 0) >= settings.ai_daily_request_limit:
        raise RecommendationError("budget_exceeded")
    item = AIRecommendation(
        review_case_id=case.id,
        evidence_snapshot_id=snapshot.id,
        taxonomy_version_id=case.taxonomy_version_id,
        requested_by_id=actor_id,
        provider=settings.ai_provider,
        model=settings.ai_model,
        model_version=settings.ai_model_version,
        prompt_version=PROMPT_VERSION,
        prompt_checksum=PROMPT_CHECKSUM,
        allowed_label_checksum=allowed_hash,
        input_checksum=_hash(snapshot.payload),
        idempotency_key=idempotency_key,
        status="pending",
    )
    db.add(item)
    await db.flush()
    db.add(
        AIRecommendationUsageReservation(
            recommendation_id=item.id, reviewer_id=actor_id, review_case_id=case.id
        )
    )
    _audit(db, actor_id, item, "ai_recommendation.requested")
    _audit(db, actor_id, item, "ai_recommendation.budget_reserved")
    return item


async def execute_recommendation(
    db: AsyncSession,
    settings: Settings,
    recommendation_id: uuid.UUID,
    provider: RecommendationProvider | None = None,
) -> AIRecommendation:
    item = await db.scalar(
        select(AIRecommendation).where(AIRecommendation.id == recommendation_id).with_for_update()
    )
    if item is None:
        raise RecommendationError("missing_recommendation")
    if item.status in TERMINAL:
        return item
    case = await db.get(ManualReviewCase, item.review_case_id)
    if not case or case.locked:
        item.status = "rejected-case-locked"
        item.failure_code = "review_case_locked"
        return item
    if item.status == "cancelled":
        return item
    snapshot = await db.get(ManualReviewEvidenceSnapshot, item.evidence_snapshot_id)
    labels = list(
        (
            await db.scalars(
                select(TaxonomyLabel).where(
                    TaxonomyLabel.taxonomy_version_id == item.taxonomy_version_id,
                    TaxonomyLabel.dimension == TaxonomyDimension.CONTENT,
                    TaxonomyLabel.status == TaxonomyLabelStatus.ACTIVE,
                )
            )
        ).all()
    )
    if snapshot is None:
        raise RecommendationError("missing_snapshot")
    allowed = sorted(label.slug for label in labels)
    item.status = "running"
    item.started_at = datetime.now(UTC)
    _audit(db, item.requested_by_id, item, "ai_recommendation.started")
    try:
        result = await (provider or recommendation_provider_for(settings)).recommend(
            snapshot.payload, allowed
        )
        output = result.output
        if not isinstance(output, RecommendationOutput):
            raise RecommendationError("invalid_output")
        requested = [output.primary_content_label, *output.secondary_content_labels]
        if any(slug not in allowed for slug in requested):
            raise RecommendationError("invalid_label")
        item.status = "completed"
        item.result = output.model_dump()
        item.confidence = round(output.confidence * 100)
        item.uncertainty_reason = output.uncertainty_reason
        item.prompt_injection_suspected = output.prompt_injection_suspected
        item.input_tokens = result.input_tokens
        item.output_tokens = result.output_tokens
        item.estimated_cost_microunits = result.estimated_cost_microunits
        item.completed_at = datetime.now(UTC)
        item.audit_disposition = "advisory_only"
        _audit(db, item.requested_by_id, item, "ai_recommendation.completed")
    except RecommendationError as exc:
        item.status = "rejected-invalid-output"
        item.failure_code = str(exc)[:80]
        item.failure_message = "Provider output was rejected"
        item.completed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.invalid_output", str(exc))
    except Exception:
        item.status = "failed"
        item.failure_code = "provider_failed"
        item.failure_message = "Provider request failed"
        item.completed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.failed")
    return item


async def cancel_recommendation(
    db: AsyncSession, *, recommendation_id: uuid.UUID, actor_id: uuid.UUID
) -> AIRecommendation:
    item = await db.get(AIRecommendation, recommendation_id)
    if item is None or item.requested_by_id != actor_id:
        raise RecommendationError("missing_recommendation")
    case = await db.get(ManualReviewCase, item.review_case_id)
    if case is None or case.locked:
        raise RecommendationError("review_case_locked")
    if item.status not in {"pending", "running"}:
        raise RecommendationError("recommendation_not_active")
    item.status = "cancelled"
    item.cancelled_at = datetime.now(UTC)
    _audit(db, actor_id, item, "ai_recommendation.cancelled")
    return item
