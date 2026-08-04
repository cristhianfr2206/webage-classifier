"""Audited, dimension-aware manual-review primitives.

This module deliberately has no task dispatch or classification entry point.
Callers must make an explicit routing decision before creating a case; a failed
or uncertain run is not automatically routed to a human reviewer.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgePolicy,
    AuditLog,
    Category,
    ClassificationAssessment,
    ClassificationRun,
    ManualReviewCase,
    ManualReviewEvidenceSnapshot,
    ManualReviewLabelDecision,
    ReviewStatus,
    TaxonomyLabel,
    TaxonomyVersion,
)
from app.taxonomy import (
    LEGACY_CATEGORY_LABEL_SLUGS,
    ReviewDecisionState,
    ReviewDisposition,
    ReviewLabelRole,
    TaxonomyDimension,
    TaxonomyLabelStatus,
    TaxonomyVersionStatus,
)

MAX_EVIDENCE_BYTES = 65_536
MAX_PROVENANCE_LENGTH = 1_000
MAX_REASON_LENGTH = 500
MAX_RATIONALE_LENGTH = 2_000
_FORBIDDEN_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|secret|token)", re.I
)
_FORBIDDEN_VALUE = re.compile(
    r"(?:\b(?:https?|ftp)://|(?:^|[\\s'\"])/(?:app|etc|home|proc|root|tmp|var)(?:/|\b)|<\s*/?\s*(?:html|script))",
    re.I,
)


class ReviewError(ValueError):
    """Base review-service error suitable for an explicit API mapping later."""


class ReviewConflictError(ReviewError):
    pass


class ReviewLockedError(ReviewError):
    pass


class ReviewValidationError(ReviewError):
    pass


@dataclass(frozen=True)
class DerivedPolicy:
    category_id: uuid.UUID | None
    age_policy_id: uuid.UUID | None


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: str, maximum: int, field: str, *, required: bool = False) -> str:
    value = value.strip()
    if required and not value:
        raise ReviewValidationError(f"{field}_required")
    if len(value) > maximum:
        raise ReviewValidationError(f"{field}_too_long")
    return value


def _validate_evidence_value(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _FORBIDDEN_KEY.search(key):
                raise ReviewValidationError("review_evidence_forbidden_key")
            _validate_evidence_value(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_evidence_value(nested)
        return
    if isinstance(value, str) and _FORBIDDEN_VALUE.search(value):
        raise ReviewValidationError("review_evidence_forbidden_value")
    if value is not None and not isinstance(value, str | int | float | bool):
        raise ReviewValidationError("review_evidence_unsupported_value")


def validate_evidence_payload(payload: dict[str, object]) -> tuple[str, int, str]:
    """Canonicalize an already-stored, plain-text evidence payload.

    URL and filesystem/path-bearing values are rejected rather than redacted:
    the review snapshot is deliberately a safe, portable evidence artifact.
    """

    if not payload:
        raise ReviewValidationError("review_evidence_required")
    _validate_evidence_value(payload)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    size = len(encoded.encode("utf-8"))
    if size > MAX_EVIDENCE_BYTES:
        raise ReviewValidationError("review_evidence_too_large")
    return encoded, size, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _case_for_update(db: AsyncSession, case_id: uuid.UUID) -> ManualReviewCase:
    case = await db.scalar(
        select(ManualReviewCase).where(ManualReviewCase.id == case_id).with_for_update()
    )
    if case is None:
        raise ReviewValidationError("review_case_not_found")
    return case


def _require_revision(case: ManualReviewCase, expected_revision: int) -> None:
    if case.revision != expected_revision:
        raise ReviewConflictError(f"review_revision_conflict:{case.revision}")


def _require_mutable(case: ManualReviewCase) -> None:
    if case.locked:
        raise ReviewLockedError("review_case_locked")


def _audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    case: ManualReviewCase,
    action: str,
    old_revision: int | None,
    reason: str = "",
    decision_id: uuid.UUID | None = None,
) -> None:
    details: dict[str, object] = {
        "old_revision": old_revision,
        "new_revision": case.revision,
        "taxonomy_version_id": str(case.taxonomy_version_id) if case.taxonomy_version_id else None,
    }
    if reason:
        details["reason"] = _bounded(reason, MAX_REASON_LENGTH, "reason")
    if decision_id is not None:
        details["decision_id"] = str(decision_id)
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="manual_review_case",
            target_id=str(case.id),
            details=details,
        )
    )


def _increment(case: ManualReviewCase) -> int:
    old_revision = case.revision
    case.revision += 1
    return old_revision


async def create_review_case(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    taxonomy_version_id: uuid.UUID,
    reason: str,
    evidence_payload: dict[str, object],
    website_id: uuid.UUID | None = None,
    classification_run_id: uuid.UUID | None = None,
    source_assessment_id: uuid.UUID | None = None,
    browser_inspection_id: uuid.UUID | None = None,
    feed_evidence_reference: str = "",
    provenance: str = "stored-classification-evidence",
    conflict_flags: list[str] | None = None,
    priority: int = 0,
) -> ManualReviewCase:
    """Create an explicitly routed case and its one immutable evidence snapshot."""

    reason = _bounded(reason, MAX_REASON_LENGTH, "reason", required=True)
    provenance = _bounded(provenance, MAX_PROVENANCE_LENGTH, "provenance", required=True)
    feed_evidence_reference = _bounded(feed_evidence_reference, 500, "feed_evidence_reference")
    version = await db.get(TaxonomyVersion, taxonomy_version_id)
    if version is None or version.status is not TaxonomyVersionStatus.PUBLISHED:
        raise ReviewValidationError("review_taxonomy_version_must_be_published")
    if source_assessment_id is not None:
        assessment = await db.get(ClassificationAssessment, source_assessment_id)
        if assessment is None or assessment.taxonomy_version_id != taxonomy_version_id:
            raise ReviewValidationError("review_source_assessment_taxonomy_mismatch")
    if (
        classification_run_id is not None
        and await db.get(ClassificationRun, classification_run_id) is None
    ):
        raise ReviewValidationError("review_source_run_not_found")
    _, payload_size, checksum = validate_evidence_payload(evidence_payload)
    case = ManualReviewCase(
        website_id=website_id,
        classification_run_id=classification_run_id,
        taxonomy_version_id=taxonomy_version_id,
        source_assessment_id=source_assessment_id,
        revision=0,
        disposition=ReviewDisposition.PENDING_REVIEW,
        conflict_flags=sorted(set(conflict_flags or [])),
        priority=priority,
        status=ReviewStatus.PENDING,
        reason=reason,
    )
    db.add(case)
    await db.flush()
    snapshot = ManualReviewEvidenceSnapshot(
        review_case_id=case.id,
        source_classification_run_id=classification_run_id,
        browser_inspection_id=browser_inspection_id,
        feed_evidence_reference=feed_evidence_reference,
        source_assessment_id=source_assessment_id,
        payload=evidence_payload,
        payload_size_bytes=payload_size,
        evidence_checksum=checksum,
        provenance=provenance,
        payload_schema_version="1",
    )
    db.add(snapshot)
    await db.flush()
    case.evidence_snapshot_id = snapshot.id
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.case_created",
        old_revision=None,
        reason=reason,
    )
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.snapshot_captured",
        old_revision=case.revision,
        reason=provenance,
    )
    return case


async def claim_review_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    claim_ttl: timedelta = timedelta(minutes=30),
) -> ManualReviewCase:
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    _require_mutable(case)
    now = _now()
    active_claim = case.claimed_by_id is not None and (
        case.claim_expires_at is None or case.claim_expires_at > now
    )
    if active_claim and case.claimed_by_id != actor_id:
        raise ReviewConflictError("review_case_already_claimed")
    if active_claim and case.claimed_by_id == actor_id:
        return case
    old_revision = _increment(case)
    case.claimed_by_id = actor_id
    case.claimed_at = now
    case.claim_expires_at = now + claim_ttl
    case.assigned_to_id = actor_id  # legacy read compatibility only
    case.status = ReviewStatus.ASSIGNED
    case.disposition = ReviewDisposition.IN_REVIEW
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.claimed",
        old_revision=old_revision,
    )
    return case


async def release_review_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    reason: str = "",
) -> ManualReviewCase:
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    _require_mutable(case)
    if case.claimed_by_id is None:
        return case
    if case.claimed_by_id != actor_id:
        raise ReviewConflictError("review_case_claimed_by_another_reviewer")
    old_revision = _increment(case)
    case.claimed_by_id = None
    case.claimed_at = None
    case.claim_expires_at = None
    case.assigned_to_id = None
    case.status = ReviewStatus.PENDING
    case.disposition = ReviewDisposition.PENDING_REVIEW
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.released",
        old_revision=old_revision,
        reason=reason,
    )
    return case


async def _derive_policy(db: AsyncSession, label: TaxonomyLabel) -> DerivedPolicy:
    legacy_slug = {value: key for key, value in LEGACY_CATEGORY_LABEL_SLUGS.items()}.get(label.slug)
    if legacy_slug is None:
        return DerivedPolicy(category_id=None, age_policy_id=None)
    category = await db.scalar(select(Category).where(Category.slug == legacy_slug))
    if category is None:
        return DerivedPolicy(category_id=None, age_policy_id=None)
    policy = await db.get(AgePolicy, category.age_policy_id) if category.age_policy_id else None
    return DerivedPolicy(
        category_id=category.id,
        age_policy_id=policy.id if policy is not None and policy.is_active else None,
    )


async def submit_label_decision(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    taxonomy_label_id: uuid.UUID,
    dimension: TaxonomyDimension,
    role: ReviewLabelRole,
    state: ReviewDecisionState,
    reviewer_confidence: int,
    rationale: str,
) -> ManualReviewLabelDecision:
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    _require_mutable(case)
    if case.claimed_by_id != actor_id:
        raise ReviewConflictError("review_case_must_be_claimed_by_reviewer")
    label = await db.get(TaxonomyLabel, taxonomy_label_id)
    if label is None or case.taxonomy_version_id != label.taxonomy_version_id:
        raise ReviewValidationError("review_label_taxonomy_mismatch")
    if label.dimension is not dimension:
        raise ReviewValidationError("review_label_dimension_mismatch")
    if role is ReviewLabelRole.PRIMARY:
        if state is not ReviewDecisionState.ACCEPTED:
            raise ReviewValidationError("review_primary_must_be_accepted")
        if (
            dimension is not TaxonomyDimension.CONTENT
            or label.status is not TaxonomyLabelStatus.ACTIVE
        ):
            raise ReviewValidationError("review_primary_must_be_active_content")
    if not 0 <= reviewer_confidence <= 100:
        raise ReviewValidationError("reviewer_confidence_out_of_range")
    rationale = _bounded(rationale, MAX_RATIONALE_LENGTH, "rationale")
    now = _now()
    if role is ReviewLabelRole.PRIMARY and state is ReviewDecisionState.ACCEPTED:
        prior = list(
            (
                await db.scalars(
                    select(ManualReviewLabelDecision).where(
                        ManualReviewLabelDecision.review_case_id == case.id,
                        ManualReviewLabelDecision.role == ReviewLabelRole.PRIMARY,
                        ManualReviewLabelDecision.state == ReviewDecisionState.ACCEPTED,
                        ManualReviewLabelDecision.superseded_at.is_(None),
                    )
                )
            ).all()
        )
        for item in prior:
            item.superseded_at = now
    decision = ManualReviewLabelDecision(
        review_case_id=case.id,
        taxonomy_label_id=label.id,
        taxonomy_version_id=label.taxonomy_version_id,
        dimension=dimension,
        role=role,
        state=state,
        reviewer_confidence=reviewer_confidence,
        rationale=rationale,
        reviewer_id=actor_id,
    )
    db.add(decision)
    await db.flush()
    old_revision = _increment(case)
    if role is ReviewLabelRole.PRIMARY and state is ReviewDecisionState.ACCEPTED:
        policy = await _derive_policy(db, label)
        case.final_category_id = policy.category_id
        case.final_age_policy_id = policy.age_policy_id
    action = f"manual_review.decision_{state.value}"
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action=action,
        old_revision=old_revision,
        decision_id=decision.id,
    )
    return decision


async def resolve_review_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    disposition: ReviewDisposition,
    reason: str = "",
) -> ManualReviewCase:
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    _require_mutable(case)
    if case.claimed_by_id != actor_id:
        raise ReviewConflictError("review_case_must_be_claimed_by_reviewer")
    if disposition in {ReviewDisposition.PENDING_REVIEW, ReviewDisposition.IN_REVIEW}:
        raise ReviewValidationError("review_disposition_must_be_terminal")
    if disposition is ReviewDisposition.RESOLVED:
        primary = await db.scalar(
            select(ManualReviewLabelDecision).where(
                ManualReviewLabelDecision.review_case_id == case.id,
                ManualReviewLabelDecision.role == ReviewLabelRole.PRIMARY,
                ManualReviewLabelDecision.state == ReviewDecisionState.ACCEPTED,
                ManualReviewLabelDecision.superseded_at.is_(None),
            )
        )
        if primary is None:
            raise ReviewValidationError("resolved_review_requires_primary_content_decision")
    old_revision = _increment(case)
    case.disposition = disposition
    case.status = (
        ReviewStatus.RESOLVED
        if disposition is ReviewDisposition.RESOLVED
        else ReviewStatus.REJECTED
    )
    case.resolved_at = _now()
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.resolved",
        old_revision=old_revision,
        reason=reason,
    )
    return case


async def lock_review_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    reason: str,
) -> ManualReviewCase:
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    _require_mutable(case)
    if case.disposition in {None, ReviewDisposition.PENDING_REVIEW, ReviewDisposition.IN_REVIEW}:
        raise ReviewValidationError("only_terminal_review_case_can_be_locked")
    reason = _bounded(reason, MAX_REASON_LENGTH, "lock_reason", required=True)
    old_revision = _increment(case)
    case.locked = True
    case.locked_by_id = actor_id
    case.locked_at = _now()
    case.lock_reason = reason
    case.status = ReviewStatus.LOCKED
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.locked",
        old_revision=old_revision,
        reason=reason,
    )
    return case


async def reopen_review_case(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    expected_revision: int,
    reason: str,
    privileged: bool,
) -> ManualReviewCase:
    if not privileged:
        raise ReviewValidationError("privileged_reopen_required")
    case = await _case_for_update(db, case_id)
    _require_revision(case, expected_revision)
    if not case.locked:
        raise ReviewValidationError("review_case_not_locked")
    reason = _bounded(reason, MAX_REASON_LENGTH, "reopen_reason", required=True)
    old_revision = _increment(case)
    case.locked = False
    case.locked_by_id = None
    case.locked_at = None
    case.reopened_by_id = actor_id
    case.reopened_at = _now()
    case.reopen_reason = reason
    case.claimed_by_id = None
    case.claimed_at = None
    case.claim_expires_at = None
    case.assigned_to_id = None
    case.status = ReviewStatus.PENDING
    case.disposition = ReviewDisposition.PENDING_REVIEW
    allowed = db.sync_session.info.setdefault("review_reopen_case_ids", set())
    if isinstance(allowed, set):
        allowed.add(case.id)
    _audit(
        db,
        actor_id=actor_id,
        case=case,
        action="manual_review.reopened",
        old_revision=old_revision,
        reason=reason,
    )
    return case
