from __future__ import annotations

import inspect
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.classification_service import (
    UT1_CATEGORY_ALIASES,
    UT1_UNSUPPORTED_CATEGORIES,
    execute_classification,
)
from app.models import Base, FeedLabelMapping, TaxonomyLabel, TaxonomyVersion
from app.taxonomy import (
    INITIAL_FEEDS_TAXONOMY_VERSION,
    INITIAL_UT1_MAPPING_VERSION,
    FeedHandlingMode,
    TaxonomyDimension,
    TaxonomyInvariantError,
    TaxonomyLabelStatus,
    TaxonomyVersionStatus,
)
from app.taxonomy_feed_evaluation import evaluate_domains
from app.taxonomy_feeds import (
    INITIAL_UT1_MAPPINGS,
    FeedSignal,
    evaluate_feed_signals,
    taxonomy_feed_version,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_initial_ut1_mappings_are_exact_and_review_gated_where_required() -> None:
    assert taxonomy_feed_version() == INITIAL_FEEDS_TAXONOMY_VERSION == "initial-feeds-v4"
    assert {
        (item.source_category, item.target_label_slug, item.handling_mode)
        for item in INITIAL_UT1_MAPPINGS
    } == {
        ("education", "education-reference", FeedHandlingMode.FINAL_CANDIDATE),
        ("social_networks", "social-networking", FeedHandlingMode.SUPPORTING_EVIDENCE),
        ("audio-video", "entertainment-streaming", FeedHandlingMode.SUPPORTING_EVIDENCE),
        ("shopping", "shopping-ecommerce", FeedHandlingMode.SUPPORTING_EVIDENCE),
        ("games", "gaming", FeedHandlingMode.SUPPORTING_EVIDENCE),
        ("adult", "adult-content", FeedHandlingMode.HIGH_RISK_EVIDENCE),
        ("gambling", "gambling", FeedHandlingMode.HIGH_RISK_EVIDENCE),
    }
    high_risk = [
        item
        for item in INITIAL_UT1_MAPPINGS
        if item.handling_mode is FeedHandlingMode.HIGH_RISK_EVIDENCE
    ]
    assert all(item.review_required for item in high_risk)
    assert all(item.mapping_version == INITIAL_UT1_MAPPING_VERSION for item in INITIAL_UT1_MAPPINGS)


async def seed_feed_taxonomy(
    session: AsyncSession,
) -> tuple[TaxonomyVersion, TaxonomyLabel, TaxonomyLabel]:
    predecessor = TaxonomyVersion(
        id=uuid.uuid4(),
        version="content-v3",
        checksum="a" * 64,
        status=TaxonomyVersionStatus.PUBLISHED,
    )
    version = TaxonomyVersion(
        id=uuid.uuid4(),
        version="feeds-v4",
        checksum="b" * 64,
        parent_version=predecessor,
    )
    content = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.CONTENT,
        slug="education-reference",
        display_name="Education & Reference",
    )
    security = TaxonomyLabel(
        id=uuid.uuid4(),
        taxonomy_version=version,
        dimension=TaxonomyDimension.SECURITY,
        slug="malware-threats",
        display_name="Malware & Threats",
    )
    session.add_all([predecessor, version, content, security])
    await session.flush()
    return version, content, security


@pytest.mark.asyncio
async def test_feed_mapping_uniqueness_and_target_constraints(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version, content, security = await seed_feed_taxonomy(session)
        mapping = FeedLabelMapping(
            taxonomy_version_id=version.id,
            source_name="ut1",
            source_category="education",
            target_label_id=content.id,
            handling_mode=FeedHandlingMode.FINAL_CANDIDATE,
            confidence=95,
            mapping_version="v1",
        )
        session.add(mapping)
        await session.flush()

        session.add(
            FeedLabelMapping(
                taxonomy_version_id=version.id,
                source_name="ut1",
                source_category="education",
                target_label_id=content.id,
                handling_mode=FeedHandlingMode.FINAL_CANDIDATE,
                confidence=95,
                mapping_version="v1",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with maker() as session:
        version, _, security = await seed_feed_taxonomy(session)
        session.add(
            FeedLabelMapping(
                taxonomy_version_id=version.id,
                source_name="ut1",
                source_category="malware",
                target_label_id=security.id,
                handling_mode=FeedHandlingMode.SUPPORTING_EVIDENCE,
                confidence=10,
                mapping_version="v1",
            )
        )
        with pytest.raises(TaxonomyInvariantError, match="feed_mapping_target_must_be_content"):
            await session.flush()


@pytest.mark.asyncio
async def test_published_feed_mappings_are_immutable_and_risk_labels_are_inactive(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version, content, _ = await seed_feed_taxonomy(session)
        risk = TaxonomyLabel(
            id=uuid.uuid4(),
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="adult-content",
            display_name="Adult Content",
            status=TaxonomyLabelStatus.INACTIVE,
            default_review_required=True,
        )
        mapping = FeedLabelMapping(
            taxonomy_version_id=version.id,
            source_name="ut1",
            source_category="education",
            target_label_id=content.id,
            handling_mode=FeedHandlingMode.FINAL_CANDIDATE,
            confidence=95,
            mapping_version="v1",
        )
        session.add_all([risk, mapping])
        await session.flush()
        version.status = TaxonomyVersionStatus.PUBLISHED
        await session.commit()

        assert risk.status is TaxonomyLabelStatus.INACTIVE
        assert risk.default_review_required is True
        mapping.enabled = False
        with pytest.raises(TaxonomyInvariantError, match="published_feed_mappings_are_immutable"):
            await session.flush()


def test_infrastructure_precedence_and_conflicts_stay_non_final() -> None:
    excluded = evaluate_feed_signals(
        (FeedSignal("ut1", "education", "exact"),), infrastructure_excluded=True
    )
    assert excluded.potential_final_candidate is None
    assert excluded.mappings[0].suppressed_by_infrastructure is True

    conflict = evaluate_feed_signals(
        (
            FeedSignal("ut1", "social_networks", "exact"),
            FeedSignal("ut1", "audio-video", "exact"),
        )
    )
    assert conflict.conflict is True
    assert conflict.requires_manual_review is True
    assert conflict.potential_final_candidate is None


def test_high_risk_and_missing_mappings_require_safe_handling() -> None:
    gambling = evaluate_feed_signals((FeedSignal("ut1", "gambling", "exact"),))
    assert gambling.requires_manual_review is True
    assert gambling.potential_final_candidate is None
    assert gambling.mappings[0].handling_mode is FeedHandlingMode.HIGH_RISK_EVIDENCE

    missing = evaluate_feed_signals((FeedSignal("ut1", "unknown-category", "exact"),))
    assert missing.mappings[0].handling_mode is FeedHandlingMode.UNSUPPORTED
    assert missing.mappings[0].target_label_slug is None
    assert missing.potential_final_candidate is None


def test_feed_evaluation_is_deterministic_and_keeps_age_and_security_out_of_mapping() -> None:
    first = evaluate_feed_signals(
        (
            FeedSignal("ut1", "education", "exact"),
            FeedSignal("ut1", "education", "exact"),
        )
    )
    second = evaluate_feed_signals(
        (
            FeedSignal("ut1", "education", "exact"),
            FeedSignal("ut1", "education", "exact"),
        )
    )
    assert first == second
    assert first.potential_final_candidate is not None
    assert first.potential_confidence == 100
    assert not hasattr(FeedLabelMapping, "age_policy_id")
    assert not hasattr(FeedLabelMapping, "policy_version_id")
    assert not hasattr(FeedLabelMapping, "security_label_id")


def test_local_feed_evaluation_uses_normalized_fixture_data_only(tmp_path: Path) -> None:
    root = tmp_path / "ut1"
    domains = root / "shopping" / "shopping" / "domains"
    domains.parent.mkdir(parents=True)
    domains.write_text("example.com\n", encoding="utf-8")

    report = evaluate_domains(("example.com", "unknown-example.com"), root)

    assert report["matched_domain_count"] == 1
    assert report["mapped_taxonomy_labels"] == {"shopping-ecommerce": 1}
    assert report["handling_modes"] == {"supporting_evidence": 1}
    assert report["potential_final_candidates"] == []
    assert report["unavailable_local_categories"] == [
        "education",
        "social_networks",
        "audio-video",
        "games",
        "adult",
        "gambling",
    ]


def test_live_ut1_pipeline_remains_unchanged() -> None:
    source = inspect.getsource(execute_classification)

    assert "taxonomy_feeds" not in source
    assert "feed_label_mappings" not in source
    assert UT1_CATEGORY_ALIASES == {"audio-video": "entertainment", "social_networks": "social"}
    assert UT1_UNSUPPORTED_CATEGORIES == {"shopping", "games"}
