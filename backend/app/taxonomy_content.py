"""Conservative, internal Phase 4 content-taxonomy projections.

This module is intentionally not imported by the live static or browser
classification services.  It defines a bounded deterministic contract for a
future rollout and for read-only evaluation of evidence that is already stored.
It therefore cannot alter current categories, policies, queue routing, or API
responses.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.taxonomy import INITIAL_CONTENT_TAXONOMY_VERSION


@dataclass(frozen=True)
class ContentLabelDefinition:
    slug: str
    display_name: str
    definition: str


@dataclass(frozen=True)
class ContentPolicyDefault:
    """A future-facing recommendation, not an active age-policy decision."""

    rating: str
    minimum_age: int | None
    review_required: bool = False
    block_recommended: bool = False


@dataclass(frozen=True)
class TaxonomyContentEvidence:
    """Bounded, already-sanitized fields available to an offline evaluator.

    A final candidate requires independent evidence families.  A single
    keyword in any field is deliberately insufficient.
    """

    title: str = ""
    description: str = ""
    visible_text: str = ""
    structured_signals: frozenset[str] = frozenset()
    infrastructure_excluded: bool = False


@dataclass(frozen=True)
class TaxonomyContentCandidate:
    label_slug: str
    confidence: int
    evidence: tuple[str, ...]
    policy_default: ContentPolicyDefault


@dataclass(frozen=True)
class TaxonomyContentResult:
    primary: TaxonomyContentCandidate | None
    secondary: tuple[TaxonomyContentCandidate, ...] = ()


NEW_CONTENT_LABELS: tuple[ContentLabelDefinition, ...] = (
    ContentLabelDefinition(
        "news-media",
        "News & Media",
        "Editorial news and media publications providing reporting or current-affairs coverage.",
    ),
    ContentLabelDefinition(
        "shopping-ecommerce",
        "Shopping & E-Commerce",
        "Consumer retail and e-commerce services offering products or transactions.",
    ),
    ContentLabelDefinition(
        "gaming",
        "Gaming",
        "Interactive digital games, game distribution, or game community services.",
    ),
    ContentLabelDefinition(
        "technology-software",
        "Technology & Software",
        "Technology and software products, documentation, downloads, or developer services.",
    ),
)

CONTENT_POLICY_DEFAULTS: dict[str, ContentPolicyDefault] = {
    "news-media": ContentPolicyDefault(rating="general", minimum_age=None),
    "shopping-ecommerce": ContentPolicyDefault(rating="general", minimum_age=None),
    "gaming": ContentPolicyDefault(rating="teen", minimum_age=13),
    "technology-software": ContentPolicyDefault(rating="general", minimum_age=None),
}

# These are evidence-only mappings.  In particular, neither mapping licenses
# a short-circuit or a final taxonomy assessment from UT1 alone.
UT1_SUPPORTING_LABELS: dict[str, str] = {
    "shopping": "shopping-ecommerce",
    "games": "gaming",
}


_TITLE_OR_DESCRIPTION_MARKERS: dict[str, frozenset[str]] = {
    "news-media": frozenset({"news", "newsroom", "headlines", "journalism"}),
    "shopping-ecommerce": frozenset({"store", "products", "catalog", "retail"}),
    "gaming": frozenset({"gaming", "play games", "esports"}),
    "technology-software": frozenset({"software", "developer", "technology", "download"}),
}
_BODY_MARKERS: dict[str, frozenset[str]] = {
    "news-media": frozenset({"breaking", "latest", "article", "subscribe"}),
    "shopping-ecommerce": frozenset({"add to cart", "shipping", "order now"}),
    "gaming": frozenset({"play now", "multiplayer", "gameplay", "esports"}),
    "technology-software": frozenset({"api", "documentation", "install", "release notes"}),
}
_STRUCTURED_SIGNALS: dict[str, frozenset[str]] = {
    "news-media": frozenset({"news-article", "editorial-index"}),
    "shopping-ecommerce": frozenset({"product-offer", "transaction-flow"}),
    "gaming": frozenset({"game-product", "interactive-game"}),
    "technology-software": frozenset({"software-application", "developer-documentation"}),
}


def ut1_supporting_candidate(source_category: str) -> TaxonomyContentCandidate | None:
    """Return bounded, deliberately non-final support from a UT1 category."""

    label_slug = UT1_SUPPORTING_LABELS.get(source_category)
    if label_slug is None:
        return None
    return TaxonomyContentCandidate(
        label_slug=label_slug,
        confidence=20,
        evidence=(f"ut1:{source_category}",),
        policy_default=CONTENT_POLICY_DEFAULTS[label_slug],
    )


def classify_taxonomy_content(evidence: TaxonomyContentEvidence) -> TaxonomyContentResult:
    """Produce at most one conservative primary content candidate.

    Every candidate needs a topical cue in title/description *and* a separate
    body cue or an independently supplied structural signal.  This prohibits a
    keyword-only final classification and makes infrastructure precedence
    explicit.  Callers decide whether/when to persist or promote the result.
    """

    if evidence.infrastructure_excluded:
        return TaxonomyContentResult(primary=None)

    title_or_description = f"{evidence.title} {evidence.description}".casefold()
    body = evidence.visible_text.casefold()
    candidates: list[TaxonomyContentCandidate] = []
    for label in CONTENT_POLICY_DEFAULTS:
        topical = _matching_marker(title_or_description, _TITLE_OR_DESCRIPTION_MARKERS[label])
        body_marker = _matching_marker(body, _BODY_MARKERS[label])
        structural = sorted(_STRUCTURED_SIGNALS[label].intersection(evidence.structured_signals))
        if topical is None or (body_marker is None and not structural):
            continue
        markers = [f"title_or_description:{topical}"]
        confidence = 82
        if body_marker is not None:
            markers.append(f"visible_text:{body_marker}")
            confidence += 6
        if structural:
            markers.extend(f"structured:{marker}" for marker in structural)
            confidence += 6
        candidates.append(
            TaxonomyContentCandidate(
                label_slug=label,
                confidence=min(confidence, 95),
                evidence=tuple(markers),
                policy_default=CONTENT_POLICY_DEFAULTS[label],
            )
        )

    ordered = sorted(
        candidates, key=lambda candidate: (-candidate.confidence, candidate.label_slug)
    )
    if not ordered:
        return TaxonomyContentResult(primary=None)
    primary = ordered[0]
    # A secondary is retained only if it independently met the same strong
    # threshold and is close enough to the primary to be meaningful.
    secondary = tuple(
        candidate for candidate in ordered[1:] if candidate.confidence >= primary.confidence - 5
    )
    return TaxonomyContentResult(primary=primary, secondary=secondary)


def _matching_marker(value: str, markers: frozenset[str]) -> str | None:
    return next((marker for marker in sorted(markers) if marker in value), None)


def taxonomy_content_version() -> str:
    """Expose the intended snapshot without importing a live classifier."""

    return INITIAL_CONTENT_TAXONOMY_VERSION
