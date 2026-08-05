"""Recommendation-only AI work for explicitly claimed, unlocked review cases."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_provider import (
    AIProviderError,
    RecommendationOutput,
    RecommendationProvider,
    recommendation_provider_for,
)
from app.config import Settings
from app.models import (
    AIRecommendation,
    AIRecommendationAttempt,
    AIRecommendationDispatchReservation,
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
TERMINAL_ATTEMPT_STATUSES = {
    "completed",
    "failed-transient",
    "failed-permanent",
    "cancelled",
    "rejected",
}
EXECUTABLE_ATTEMPT_STATUSES = {"pending", "dispatched"}
TRANSIENT_FAILURES = {
    "provider_timeout",
    "provider_unavailable",
    "provider_rate_limited",
    "worker_lost",
}


def failure_class(code: str) -> tuple[str, bool]:
    return ("transient", True) if code in TRANSIENT_FAILURES else ("permanent", False)


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


async def _active_content_labels(
    db: AsyncSession, taxonomy_version_id: uuid.UUID
) -> tuple[list[TaxonomyLabel], str]:
    labels = list(
        (
            await db.scalars(
                select(TaxonomyLabel)
                .where(
                    TaxonomyLabel.taxonomy_version_id == taxonomy_version_id,
                    TaxonomyLabel.dimension == TaxonomyDimension.CONTENT,
                    TaxonomyLabel.status == TaxonomyLabelStatus.ACTIVE,
                )
                .order_by(TaxonomyLabel.slug)
            )
        ).all()
    )
    return labels, _hash([label.slug for label in labels])


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
    labels, allowed_hash = await _active_content_labels(db, case.taxonomy_version_id)
    if not labels:
        raise RecommendationError("no_active_content_labels")
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
    db.add(
        AIRecommendationAttempt(
            recommendation_id=item.id,
            attempt_number=1,
            provider=item.provider,
            model=item.model,
            model_version=item.model_version,
            idempotency_key=idempotency_key,
            requested_by_id=actor_id,
        )
    )
    return item


async def retry_recommendation(
    db: AsyncSession,
    settings: Settings,
    *,
    recommendation_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    idempotency_key: str,
) -> AIRecommendationAttempt:
    if not settings.ai_enabled or settings.ai_recommendation_mode != "manual_only":
        raise RecommendationError("ai_disabled")
    item = await db.scalar(
        select(AIRecommendation).where(AIRecommendation.id == recommendation_id).with_for_update()
    )
    if item is None or item.requested_by_id != actor_id:
        raise RecommendationError("missing_recommendation")
    case = await db.get(ManualReviewCase, item.review_case_id)
    if case is None or case.locked:
        raise RecommendationError("review_case_locked")
    if case.disposition and case.disposition.value == "cancelled":
        raise RecommendationError("review_case_cancelled")
    if case.revision != expected_revision:
        raise RecommendationError("review_revision_conflict")
    if case.taxonomy_version_id != item.taxonomy_version_id:
        raise RecommendationError("taxonomy_version_changed")
    if case.evidence_snapshot_id != item.evidence_snapshot_id or not await db.get(
        ManualReviewEvidenceSnapshot, item.evidence_snapshot_id
    ):
        raise RecommendationError("review_case_missing_snapshot")
    labels, allowed_hash = await _active_content_labels(db, item.taxonomy_version_id)
    if not labels or allowed_hash != item.allowed_label_checksum:
        raise RecommendationError("allowed_labels_changed")
    existing = await db.scalar(
        select(AIRecommendationAttempt).where(
            AIRecommendationAttempt.recommendation_id == item.id,
            AIRecommendationAttempt.idempotency_key == idempotency_key,
            AIRecommendationAttempt.retry_of_attempt_id.is_not(None),
        )
    )
    if existing is not None:
        return existing
    latest = await db.scalar(
        select(AIRecommendationAttempt)
        .where(AIRecommendationAttempt.recommendation_id == item.id)
        .order_by(AIRecommendationAttempt.attempt_number.desc())
    )
    if latest is None or not latest.retryable or latest.status != "failed-transient":
        raise RecommendationError("recommendation_not_retryable")
    if latest.attempt_number > settings.ai_max_retries:
        raise RecommendationError("retry_limit_exceeded")
    attempt = AIRecommendationAttempt(
        recommendation_id=item.id,
        attempt_number=latest.attempt_number + 1,
        provider=item.provider,
        model=item.model,
        model_version=item.model_version,
        idempotency_key=idempotency_key,
        requested_by_id=actor_id,
        retry_of_attempt_id=latest.id,
    )
    db.add(attempt)
    await db.flush()
    item.status = "pending"
    item.failure_code = None
    item.failure_message = ""
    item.completed_at = None
    item.cancelled_at = None
    item.retry_count = latest.attempt_number
    _audit(db, actor_id, item, "ai_recommendation.retry_requested")
    _audit(db, actor_id, item, "ai_recommendation.attempt_created")
    return attempt


async def reserve_attempt_dispatch(
    db: AsyncSession, *, attempt_id: uuid.UUID, queue_name: str
) -> tuple[AIRecommendationDispatchReservation, bool]:
    if queue_name not in {"ai", "ai_realtime"}:
        raise RecommendationError("invalid_recommendation_queue")
    attempt = await db.scalar(
        select(AIRecommendationAttempt)
        .where(AIRecommendationAttempt.id == attempt_id)
        .with_for_update()
    )
    if attempt is None:
        raise RecommendationError("attempt_not_dispatchable")
    existing = await db.scalar(
        select(AIRecommendationDispatchReservation).where(
            AIRecommendationDispatchReservation.recommendation_attempt_id == attempt.id
        )
    )
    if existing:
        if existing.state == "failed":
            task_id = str(uuid.uuid4())
            existing.task_id = task_id
            existing.queue_name = queue_name
            existing.state = "dispatching"
            existing.failure_reason = ""
            existing.released_at = None
            attempt.task_id = task_id
            attempt.queue_name = queue_name
            attempt.status = "dispatched"
            attempt.dispatched_at = datetime.now(UTC)
            item = await db.get(AIRecommendation, attempt.recommendation_id)
            if item is not None:
                _audit(db, item.requested_by_id, item, "ai_recommendation.dispatch_reserved")
            return existing, True
        item = await db.get(AIRecommendation, attempt.recommendation_id)
        if item is not None:
            _audit(db, item.requested_by_id, item, "ai_recommendation.duplicate_dispatch_prevented")
        return existing, False
    if attempt.status != "pending":
        raise RecommendationError("attempt_not_dispatchable")
    task_id = str(uuid.uuid4())
    reservation = AIRecommendationDispatchReservation(
        recommendation_attempt_id=attempt.id,
        dispatch_key=_hash([str(attempt.id), attempt.attempt_number]),
        task_id=task_id,
        queue_name=queue_name,
        state="dispatching",
    )
    db.add(reservation)
    attempt.task_id = task_id
    attempt.queue_name = queue_name
    attempt.status = "dispatched"
    attempt.dispatched_at = datetime.now(UTC)
    item = await db.get(AIRecommendation, attempt.recommendation_id)
    if item:
        _audit(db, item.requested_by_id, item, "ai_recommendation.dispatch_reserved")
    return reservation, True


async def mark_dispatch_result(
    db: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    succeeded: bool,
) -> None:
    reservation = await db.scalar(
        select(AIRecommendationDispatchReservation)
        .where(AIRecommendationDispatchReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None or reservation.state != "dispatching":
        return
    attempt = await db.get(AIRecommendationAttempt, reservation.recommendation_attempt_id)
    item = await db.get(AIRecommendation, attempt.recommendation_id) if attempt else None
    if succeeded:
        reservation.state = "dispatched"
        reservation.dispatched_at = datetime.now(UTC)
        if item is not None:
            _audit(db, item.requested_by_id, item, "ai_recommendation.dispatch_succeeded")
    else:
        reservation.state = "failed"
        reservation.released_at = datetime.now(UTC)
        reservation.failure_reason = "enqueue_failed"
        if attempt is not None:
            attempt.status = "pending"
            attempt.task_id = None
            attempt.queue_name = None
            attempt.dispatched_at = None
        if item is not None:
            _audit(db, item.requested_by_id, item, "ai_recommendation.dispatch_failed")


async def execute_recommendation(
    db: AsyncSession,
    settings: Settings,
    recommendation_id: uuid.UUID,
    provider: RecommendationProvider | None = None,
    attempt_id: uuid.UUID | None = None,
) -> AIRecommendation:
    item = await db.scalar(
        select(AIRecommendation).where(AIRecommendation.id == recommendation_id).with_for_update()
    )
    if item is None:
        raise RecommendationError("missing_recommendation")
    if item.status in TERMINAL:
        return item
    attempt_query = select(AIRecommendationAttempt).where(
        AIRecommendationAttempt.recommendation_id == item.id
    )
    if attempt_id is not None:
        attempt_query = attempt_query.where(AIRecommendationAttempt.id == attempt_id)
    else:
        attempt_query = attempt_query.order_by(AIRecommendationAttempt.attempt_number.desc())
    attempt = await db.scalar(attempt_query)
    if attempt is None or attempt.status in TERMINAL_ATTEMPT_STATUSES:
        _audit(db, item.requested_by_id, item, "ai_recommendation.duplicate_execution_ignored")
        return item
    case = await db.get(ManualReviewCase, item.review_case_id)
    if not case or case.locked or case.disposition and case.disposition.value == "cancelled":
        code = "review_case_locked" if not case or case.locked else "review_case_cancelled"
        item.status = "rejected-case-locked" if code == "review_case_locked" else "failed"
        item.failure_code = code
        attempt.status = "rejected"
        attempt.failure_class, attempt.retryable = failure_class(code)
        attempt.failure_code = code
        attempt.failure_message = "Recommendation cannot run for this review case"
        attempt.failed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.attempt_failed_permanently")
        return item
    if item.status == "cancelled" or attempt.status == "cancelled":
        return item
    if attempt.status not in EXECUTABLE_ATTEMPT_STATUSES:
        raise RecommendationError("invalid_attempt_transition")
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
    attempt.status = "running"
    attempt.started_at = datetime.now(UTC)
    item.started_at = datetime.now(UTC)
    _audit(db, item.requested_by_id, item, "ai_recommendation.started")
    _audit(db, item.requested_by_id, item, "ai_recommendation.attempt_started")
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
        attempt.status = "completed"
        attempt.completed_at = datetime.now(UTC)
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
        _audit(db, item.requested_by_id, item, "ai_recommendation.attempt_completed")
    except RecommendationError as exc:
        item.status = "rejected-invalid-output"
        item.failure_code = str(exc)[:80]
        item.failure_message = "Provider output was rejected"
        item.completed_at = datetime.now(UTC)
        attempt.status = "failed-permanent"
        attempt.failure_class, attempt.retryable = failure_class(str(exc))
        attempt.failure_code = str(exc)[:80]
        attempt.failure_message = "Provider output was rejected"
        attempt.failed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.invalid_output", str(exc))
        _audit(db, item.requested_by_id, item, "ai_recommendation.attempt_failed_permanently")
    except AIProviderError as exc:
        item.status = "failed"
        item.failure_code = str(exc)[:80]
        item.failure_message = "Provider request failed"
        item.completed_at = datetime.now(UTC)
        attempt.failure_class, attempt.retryable = failure_class(str(exc))
        attempt.status = "failed-transient" if attempt.retryable else "failed-permanent"
        attempt.failure_code = str(exc)[:80]
        attempt.failure_message = "Provider request failed"
        attempt.failed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.failed")
        _audit(
            db,
            item.requested_by_id,
            item,
            "ai_recommendation.attempt_failed_transiently"
            if attempt.retryable
            else "ai_recommendation.attempt_failed_permanently",
        )
    except Exception:
        item.status = "failed"
        item.failure_code = "provider_failed"
        item.failure_message = "Provider request failed"
        item.completed_at = datetime.now(UTC)
        attempt.failure_class, attempt.retryable = failure_class("provider_failed")
        attempt.status = "failed-permanent"
        attempt.failure_code = "provider_failed"
        attempt.failure_message = "Provider request failed"
        attempt.failed_at = datetime.now(UTC)
        _audit(db, item.requested_by_id, item, "ai_recommendation.failed")
        _audit(db, item.requested_by_id, item, "ai_recommendation.attempt_failed_permanently")
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
    attempt = await db.scalar(
        select(AIRecommendationAttempt)
        .where(AIRecommendationAttempt.recommendation_id == item.id)
        .order_by(AIRecommendationAttempt.attempt_number.desc())
    )
    if attempt is not None and attempt.status in {"pending", "dispatched"}:
        attempt.status = "cancelled"
        attempt.cancelled_at = datetime.now(UTC)
    _audit(db, actor_id, item, "ai_recommendation.cancelled")
    _audit(db, actor_id, item, "ai_recommendation.attempt_cancelled")
    return item


async def reconcile_stale_recommendation_attempts(
    db: AsyncSession, settings: Settings, *, actor_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Fail only demonstrably abandoned advisory attempts; never enqueue or infer."""
    now = datetime.now(UTC)
    counters = {
        "scanned": 0,
        "failed_transient": 0,
        "released_reservations": 0,
        "skipped_locked": 0,
        "skipped_cancelled": 0,
        "skipped_terminal": 0,
        "skipped_invalid": 0,
        "lease_prevented": 0,
    }
    candidates = list(
        (
            await db.scalars(
                select(AIRecommendationAttempt)
                .where(AIRecommendationAttempt.status.in_(("pending", "dispatched", "running")))
                .order_by(AIRecommendationAttempt.created_at, AIRecommendationAttempt.id)
                .limit(settings.ai_reconciliation_batch_size)
            )
        ).all()
    )
    for candidate in candidates:
        counters["scanned"] += 1
        attempt = await db.scalar(
            select(AIRecommendationAttempt)
            .where(AIRecommendationAttempt.id == candidate.id)
            .with_for_update()
        )
        if attempt is None or attempt.status in TERMINAL_ATTEMPT_STATUSES:
            counters["skipped_terminal"] += 1
            continue
        if (
            attempt.maintenance_lease_expires_at is not None
            and attempt.maintenance_lease_expires_at > now
        ):
            counters["lease_prevented"] += 1
            continue
        lease_recovered = attempt.maintenance_lease_expires_at is not None
        attempt.maintenance_lease_id = str(uuid.uuid4())
        attempt.maintenance_lease_expires_at = now + timedelta(
            seconds=settings.ai_reconciliation_lease_seconds
        )
        item = await db.get(AIRecommendation, attempt.recommendation_id)
        case = await db.get(ManualReviewCase, item.review_case_id) if item else None
        if (
            not item
            or not case
            or not await db.get(ManualReviewEvidenceSnapshot, item.evidence_snapshot_id)
        ):
            counters["skipped_invalid"] += 1
            _finish_reconciliation(attempt, now, "invalid_reconciliation_context")
            continue
        if case.locked:
            counters["skipped_locked"] += 1
            _audit(
                db, item.requested_by_id, item, "ai_recommendation.reconciliation_skipped_locked"
            )
            _finish_reconciliation(attempt, now, "review_case_locked")
            continue
        if case.disposition and case.disposition.value == "cancelled" or item.status == "cancelled":
            counters["skipped_cancelled"] += 1
            _audit(
                db, item.requested_by_id, item, "ai_recommendation.reconciliation_skipped_cancelled"
            )
            _finish_reconciliation(attempt, now, "review_case_cancelled")
            continue
        labels, allowed_hash = await _active_content_labels(db, item.taxonomy_version_id)
        if (
            case.taxonomy_version_id != item.taxonomy_version_id
            or not labels
            or allowed_hash != item.allowed_label_checksum
        ):
            counters["skipped_invalid"] += 1
            _finish_reconciliation(attempt, now, "taxonomy_or_labels_changed")
            continue
        reservation = await db.scalar(
            select(AIRecommendationDispatchReservation).where(
                AIRecommendationDispatchReservation.recommendation_attempt_id == attempt.id
            )
        )
        reason = _stale_reason(attempt, reservation, now, settings)
        if reason is None:
            _finish_reconciliation(attempt, now, "fresh")
            continue
        if lease_recovered:
            _audit(db, item.requested_by_id, item, "ai_recommendation.maintenance_lease_recovered")
        _audit(db, item.requested_by_id, item, f"ai_recommendation.stale_{attempt.status}_detected")
        if reservation is not None and reservation.state in {"reserved", "dispatching"}:
            reservation.state = "failed"
            reservation.released_at = now
            reservation.failure_reason = "stale_enqueue"
            counters["released_reservations"] += 1
            _audit(
                db, item.requested_by_id, item, "ai_recommendation.dispatch_reservation_released"
            )
        attempt.status = "failed-transient"
        attempt.failure_class = "transient"
        attempt.retryable = True
        attempt.failure_code = reason
        attempt.failure_message = "Recommendation attempt did not complete"
        attempt.failed_at = now
        item.status = "failed"
        item.failure_code = reason
        item.failure_message = "Recommendation attempt did not complete"
        item.completed_at = now
        counters["failed_transient"] += 1
        _audit(
            db, item.requested_by_id, item, "ai_recommendation.attempt_failed_transiently", reason
        )
        _finish_reconciliation(attempt, now, reason)
    return counters


def _finish_reconciliation(attempt: AIRecommendationAttempt, now: datetime, reason: str) -> None:
    attempt.reconciliation_count += 1
    attempt.last_reconciled_at = now
    attempt.last_reconciliation_reason = reason[:120]
    attempt.maintenance_lease_id = None
    attempt.maintenance_lease_expires_at = None


def _stale_reason(
    attempt: AIRecommendationAttempt,
    reservation: AIRecommendationDispatchReservation | None,
    now: datetime,
    settings: Settings,
) -> str | None:
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    if attempt.status == "pending":
        if reservation is not None and reservation.state in {"reserved", "dispatching"}:
            if utc(reservation.reserved_at) <= now - timedelta(
                seconds=settings.ai_reconciliation_pending_seconds
            ):
                return "stale_dispatch_reservation"
            return None
        if utc(attempt.requested_at) <= now - timedelta(
            seconds=settings.ai_reconciliation_pending_seconds
        ):
            return "stale_pending"
    if (
        attempt.status == "dispatched"
        and attempt.dispatched_at
        and utc(attempt.dispatched_at)
        <= now - timedelta(seconds=settings.ai_reconciliation_dispatched_seconds)
    ):
        return "stale_dispatched"
    if (
        attempt.status == "running"
        and attempt.started_at
        and utc(attempt.started_at)
        <= now - timedelta(seconds=settings.ai_reconciliation_running_seconds)
    ):
        return "stale_running"
    return None
