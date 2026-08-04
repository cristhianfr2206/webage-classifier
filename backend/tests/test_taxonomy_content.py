from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.classification_service import (
    UT1_CATEGORY_ALIASES,
    UT1_UNSUPPORTED_CATEGORIES,
    execute_classification,
)
from app.models import Base, TaxonomyLabel, TaxonomyVersion
from app.taxonomy import (
    INFRASTRUCTURE_SCOPE_LABEL_SLUGS,
    INITIAL_CONTENT_LABEL_SLUGS,
    INITIAL_CONTENT_TAXONOMY_VERSION,
    INITIAL_SCOPE_TAXONOMY_VERSION,
    TaxonomyDimension,
    TaxonomyVersionStatus,
)
from app.taxonomy_content import (
    CONTENT_POLICY_DEFAULTS,
    NEW_CONTENT_LABELS,
    UT1_SUPPORTING_LABELS,
    TaxonomyContentEvidence,
    classify_taxonomy_content,
    taxonomy_content_version,
    ut1_supporting_candidate,
)
from app.taxonomy_content_evaluation import evaluate_stored_row


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_content_successor_label_set_and_policy_defaults_are_exact() -> None:
    assert INITIAL_CONTENT_TAXONOMY_VERSION == "initial-content-v3"
    assert taxonomy_content_version() == INITIAL_CONTENT_TAXONOMY_VERSION
    assert {label.slug for label in NEW_CONTENT_LABELS} == {
        "news-media",
        "shopping-ecommerce",
        "gaming",
        "technology-software",
    }
    assert INITIAL_CONTENT_LABEL_SLUGS == {
        "education-reference",
        "entertainment-streaming",
        "social-networking",
        "news-media",
        "shopping-ecommerce",
        "gaming",
        "technology-software",
    }
    assert set(INFRASTRUCTURE_SCOPE_LABEL_SLUGS.values()) == {
        "analytics-advertising",
        "cdn-delivery",
        "cloud-hosting",
        "dns-nameserver",
        "software-update",
        "static-asset-host",
        "time-service",
    }
    assert CONTENT_POLICY_DEFAULTS["news-media"].rating == "general"
    assert CONTENT_POLICY_DEFAULTS["shopping-ecommerce"].minimum_age is None
    assert CONTENT_POLICY_DEFAULTS["gaming"].rating == "teen"
    assert CONTENT_POLICY_DEFAULTS["gaming"].minimum_age == 13
    assert CONTENT_POLICY_DEFAULTS["technology-software"].rating == "general"
    assert all(not default.block_recommended for default in CONTENT_POLICY_DEFAULTS.values())


@pytest.mark.asyncio
async def test_content_successor_is_a_publishable_immutable_snapshot(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        predecessor = TaxonomyVersion(
            id=uuid.uuid4(),
            version=INITIAL_SCOPE_TAXONOMY_VERSION,
            checksum="a" * 64,
            status=TaxonomyVersionStatus.PUBLISHED,
        )
        successor = TaxonomyVersion(
            id=uuid.uuid4(),
            version=INITIAL_CONTENT_TAXONOMY_VERSION,
            checksum="b" * 64,
            parent_version=predecessor,
        )
        labels = [
            TaxonomyLabel(
                id=uuid.uuid4(),
                taxonomy_version=successor,
                dimension=TaxonomyDimension.CONTENT,
                slug=definition.slug,
                display_name=definition.display_name,
                definition=definition.definition,
            )
            for definition in NEW_CONTENT_LABELS
        ]
        session.add_all([predecessor, successor, *labels])
        await session.flush()
        successor.status = TaxonomyVersionStatus.PUBLISHED
        await session.commit()

        assert successor.parent_version_id == predecessor.id
        assert successor.status is TaxonomyVersionStatus.PUBLISHED
        assert {label.slug for label in labels} == {label.slug for label in NEW_CONTENT_LABELS}


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            TaxonomyContentEvidence(
                title="Daily news and headlines",
                description="Independent reporting",
                visible_text="Read the latest article and subscribe for updates.",
            ),
            "news-media",
        ),
        (
            TaxonomyContentEvidence(
                title="Outdoor products store",
                description="Shop equipment online",
                visible_text="Add to cart, choose shipping, then checkout.",
            ),
            "shopping-ecommerce",
        ),
        (
            TaxonomyContentEvidence(
                title="Gaming community",
                description="Play new games",
                visible_text="Play now with multiplayer gameplay and esports events.",
            ),
            "gaming",
        ),
        (
            TaxonomyContentEvidence(
                title="Developer software downloads",
                description="Technology tools for teams",
                visible_text="Read our API documentation and install the latest release notes.",
            ),
            "technology-software",
        ),
    ],
)
def test_strong_independent_evidence_produces_one_primary_content_label(
    evidence: TaxonomyContentEvidence, expected: str
) -> None:
    result = classify_taxonomy_content(evidence)

    assert result.primary is not None
    assert result.primary.label_slug == expected
    assert result.primary.confidence >= 88
    assert result.primary.label_slug not in {item.label_slug for item in result.secondary}


def test_weak_keyword_evidence_does_not_finalize_a_content_label() -> None:
    result = classify_taxonomy_content(
        TaxonomyContentEvidence(title="Gaming", description="", visible_text="Welcome")
    )

    assert result.primary is None
    assert result.secondary == ()


def test_infrastructure_precedence_prevents_any_content_candidate() -> None:
    result = classify_taxonomy_content(
        TaxonomyContentEvidence(
            title="Online products store",
            visible_text="Add to cart and checkout.",
            infrastructure_excluded=True,
        )
    )

    assert result.primary is None
    assert result.secondary == ()


@pytest.mark.parametrize(
    ("feed_category", "label"),
    [("shopping", "shopping-ecommerce"), ("games", "gaming")],
)
def test_ut1_shopping_and_games_are_supporting_evidence_only(
    feed_category: str, label: str
) -> None:
    candidate = ut1_supporting_candidate(feed_category)

    assert candidate is not None
    assert candidate.label_slug == label
    assert candidate.confidence == 20
    assert candidate.evidence == (f"ut1:{feed_category}",)
    assert UT1_SUPPORTING_LABELS[feed_category] == label
    assert feed_category in UT1_UNSUPPORTED_CATEGORIES
    assert feed_category not in UT1_CATEGORY_ALIASES


def test_existing_live_pipeline_does_not_import_or_use_phase_four_rules() -> None:
    source = inspect.getsource(execute_classification)

    assert "classify_taxonomy_content" not in source
    assert "taxonomy_content" not in source
    assert UT1_CATEGORY_ALIASES == {"audio-video": "entertainment", "social_networks": "social"}


def test_offline_evaluation_uses_only_stored_fields() -> None:
    report = evaluate_stored_row(
        title="Acme products store",
        description="Shop home equipment",
        text_excerpt="Add to cart and checkout with shipping options.",
        existing_category="education",
    )

    assert report["candidate"] == "shopping-ecommerce"
    assert report["existing_category"] == "education"
    assert report["conflicts_existing_label"] is True
