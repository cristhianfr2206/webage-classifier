"""Versioned, evaluation-only public-feed mappings for the taxonomy rollout.

This module is not imported by the live UT1 or classification services.  It
defines a bounded mapping contract and conflict policy for offline evaluation;
it cannot short-circuit inspection, assign an age policy, set a security label,
or enforce a block decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.taxonomy import (
    INITIAL_FEEDS_TAXONOMY_VERSION,
    INITIAL_UT1_MAPPING_VERSION,
    FeedHandlingMode,
)


@dataclass(frozen=True)
class FeedMappingDefinition:
    source_name: str
    source_category: str
    target_label_slug: str
    handling_mode: FeedHandlingMode
    confidence: int
    review_required: bool
    mapping_version: str = INITIAL_UT1_MAPPING_VERSION
    enabled: bool = True


@dataclass(frozen=True)
class FeedSignal:
    source_name: str
    source_category: str
    match_type: str
    feed_timestamp: datetime | None = None


@dataclass(frozen=True)
class MappedFeedSignal:
    original_source: str
    original_source_category: str
    target_label_slug: str | None
    handling_mode: FeedHandlingMode
    confidence: int
    review_required: bool
    mapping_version: str | None
    feed_timestamp: datetime | None
    match_type: str
    suppressed_by_infrastructure: bool = False


@dataclass(frozen=True)
class FeedEvaluation:
    mappings: tuple[MappedFeedSignal, ...]
    potential_final_candidate: MappedFeedSignal | None
    potential_confidence: int | None
    requires_manual_review: bool
    conflict: bool


INITIAL_UT1_MAPPINGS: tuple[FeedMappingDefinition, ...] = (
    FeedMappingDefinition(
        "ut1", "education", "education-reference", FeedHandlingMode.FINAL_CANDIDATE, 95, False
    ),
    FeedMappingDefinition(
        "ut1",
        "social_networks",
        "social-networking",
        FeedHandlingMode.SUPPORTING_EVIDENCE,
        20,
        False,
    ),
    FeedMappingDefinition(
        "ut1",
        "audio-video",
        "entertainment-streaming",
        FeedHandlingMode.SUPPORTING_EVIDENCE,
        20,
        False,
    ),
    FeedMappingDefinition(
        "ut1", "shopping", "shopping-ecommerce", FeedHandlingMode.SUPPORTING_EVIDENCE, 20, False
    ),
    FeedMappingDefinition(
        "ut1", "games", "gaming", FeedHandlingMode.SUPPORTING_EVIDENCE, 20, False
    ),
    FeedMappingDefinition(
        "ut1", "adult", "adult-content", FeedHandlingMode.HIGH_RISK_EVIDENCE, 75, True
    ),
    FeedMappingDefinition(
        "ut1", "gambling", "gambling", FeedHandlingMode.HIGH_RISK_EVIDENCE, 75, True
    ),
)


def mapping_for_signal(signal: FeedSignal) -> FeedMappingDefinition | None:
    """Find one enabled mapping without changing source provenance."""

    return next(
        (
            mapping
            for mapping in INITIAL_UT1_MAPPINGS
            if mapping.enabled
            and mapping.source_name == signal.source_name
            and mapping.source_category == signal.source_category
        ),
        None,
    )


def evaluate_feed_signals(
    signals: tuple[FeedSignal, ...], *, infrastructure_excluded: bool = False
) -> FeedEvaluation:
    """Map feed signals without authorizing a final classification.

    A safe exact infrastructure exclusion suppresses every prospective feed
    label. Multiple distinct target labels are treated as a conflict and need
    review; repeated agreeing signals only raise *evidence* confidence.
    """

    mapped: list[MappedFeedSignal] = []
    for signal in signals:
        definition = mapping_for_signal(signal)
        if definition is None:
            mapped.append(
                MappedFeedSignal(
                    original_source=signal.source_name,
                    original_source_category=signal.source_category,
                    target_label_slug=None,
                    handling_mode=FeedHandlingMode.UNSUPPORTED,
                    confidence=0,
                    review_required=False,
                    mapping_version=None,
                    feed_timestamp=signal.feed_timestamp,
                    match_type=signal.match_type,
                    suppressed_by_infrastructure=infrastructure_excluded,
                )
            )
            continue
        mapped.append(
            MappedFeedSignal(
                original_source=signal.source_name,
                original_source_category=signal.source_category,
                target_label_slug=definition.target_label_slug,
                handling_mode=definition.handling_mode,
                confidence=definition.confidence,
                review_required=definition.review_required,
                mapping_version=definition.mapping_version,
                feed_timestamp=signal.feed_timestamp,
                match_type=signal.match_type,
                suppressed_by_infrastructure=infrastructure_excluded,
            )
        )

    active = [item for item in mapped if not item.suppressed_by_infrastructure]
    target_labels = {
        item.target_label_slug for item in active if item.target_label_slug is not None
    }
    conflict = len(target_labels) > 1
    final_candidates = [
        item for item in active if item.handling_mode is FeedHandlingMode.FINAL_CANDIDATE
    ]
    final_target_labels = {item.target_label_slug for item in final_candidates}
    potential_final_candidate = (
        final_candidates[0] if len(final_target_labels) == 1 and not conflict else None
    )
    if potential_final_candidate is None:
        potential_confidence = None
    else:
        agreements = sum(
            item.target_label_slug == potential_final_candidate.target_label_slug for item in active
        )
        potential_confidence = min(100, potential_final_candidate.confidence + 5 * (agreements - 1))
    requires_manual_review = conflict or any(item.review_required for item in active)
    return FeedEvaluation(
        mappings=tuple(mapped),
        potential_final_candidate=potential_final_candidate,
        potential_confidence=potential_confidence,
        requires_manual_review=requires_manual_review,
        conflict=conflict,
    )


def taxonomy_feed_version() -> str:
    return INITIAL_FEEDS_TAXONOMY_VERSION
