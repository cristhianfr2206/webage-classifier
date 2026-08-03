"""Internal projections for future taxonomy assessment persistence.

Phase 3 does not call this helper from the classification pipeline. It keeps the
legacy backfill and a future infrastructure exclusion writer on the same bounded
scope-label contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.taxonomy import (
    INFRASTRUCTURE_SCOPE_LABEL_SLUGS,
    AssessmentDisposition,
    AssessmentLabelSource,
)

INFRASTRUCTURE_ERROR_PREFIX = "non_consumer_infrastructure:"


@dataclass(frozen=True)
class InfrastructureAssessmentProjection:
    terminal_disposition: AssessmentDisposition
    scope_label_slug: str
    source: AssessmentLabelSource
    evidence: str
    provenance: str


def infrastructure_assessment_projection(
    error_code: str,
) -> InfrastructureAssessmentProjection | None:
    """Return a bounded scope-only assessment projection for a known exclusion."""

    if not error_code.startswith(INFRASTRUCTURE_ERROR_PREFIX):
        return None
    infrastructure_type = error_code.removeprefix(INFRASTRUCTURE_ERROR_PREFIX)
    scope_label_slug = INFRASTRUCTURE_SCOPE_LABEL_SLUGS.get(infrastructure_type)
    if scope_label_slug is None:
        return None
    return InfrastructureAssessmentProjection(
        terminal_disposition=AssessmentDisposition.NON_CONSUMER_INFRASTRUCTURE,
        scope_label_slug=scope_label_slug,
        source=AssessmentLabelSource.INFRASTRUCTURE,
        evidence=error_code,
        provenance="infrastructure-detector",
    )
