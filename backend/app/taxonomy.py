"""Taxonomy storage types and invariant helpers.

This module deliberately contains no classification, feed, or policy-selection
logic. Phase 1 only defines the storage contract for a later taxonomy rollout.
"""

from __future__ import annotations

import enum


class TaxonomyVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class TaxonomyLabelStatus(str, enum.Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class TaxonomyDimension(str, enum.Enum):
    CONTENT = "content"
    SECURITY = "security"
    SCOPE = "scope"


class AssessmentDisposition(str, enum.Enum):
    UNRESOLVED = "unresolved"
    CLASSIFIED = "classified"
    UNCATEGORIZED = "uncategorized"
    NON_CONSUMER_INFRASTRUCTURE = "non_consumer_infrastructure"
    UNREACHABLE = "unreachable"
    SAFETY_REJECTED = "safety_rejected"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AssessmentLabelRole(str, enum.Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    EVIDENCE = "evidence"


class AssessmentLabelSource(str, enum.Enum):
    RULES = "rules"
    STATIC = "static"
    RENDERED = "rendered"
    SCREENSHOT = "screenshot"
    UT1 = "ut1"
    INFRASTRUCTURE = "infrastructure"
    AI = "ai"
    MANUAL = "manual"
    LEGACY_BACKFILL = "legacy-backfill"


INITIAL_LEGACY_TAXONOMY_VERSION = "initial-legacy-v1"
INITIAL_SCOPE_TAXONOMY_VERSION = "initial-scope-v2"
INITIAL_CONTENT_TAXONOMY_VERSION = "initial-content-v3"
LEGACY_CATEGORY_LABEL_SLUGS: dict[str, str] = {
    "education": "education-reference",
    "entertainment": "entertainment-streaming",
    "social": "social-networking",
}
INFRASTRUCTURE_SCOPE_LABEL_SLUGS: dict[str, str] = {
    "analytics_advertising": "analytics-advertising",
    "cdn_delivery": "cdn-delivery",
    "cloud_hosting": "cloud-hosting",
    "dns_nameserver": "dns-nameserver",
    "software_update": "software-update",
    "static_asset_host": "static-asset-host",
    "time_service": "time-service",
}

# Phase 4 keeps these labels internal to the taxonomy rollout.  They are not
# mapped into the legacy Category table and they are not consulted by the live
# classification pipeline yet.
INITIAL_CONTENT_LABEL_SLUGS = frozenset(
    {
        "education-reference",
        "entertainment-streaming",
        "social-networking",
        "news-media",
        "shopping-ecommerce",
        "gaming",
        "technology-software",
    }
)


class TaxonomyInvariantError(ValueError):
    """Raised before persistence when a taxonomy invariant is violated."""


def validate_label_assignment(
    *,
    label_dimension: TaxonomyDimension,
    assignment_dimension: TaxonomyDimension,
    role: AssessmentLabelRole,
) -> None:
    """Validate the dimension and role stored with an assessment-label link."""

    if assignment_dimension is not label_dimension:
        raise TaxonomyInvariantError("assessment_label_dimension_mismatch")
    if role is AssessmentLabelRole.PRIMARY and label_dimension is not TaxonomyDimension.CONTENT:
        raise TaxonomyInvariantError("primary_label_must_be_content")
