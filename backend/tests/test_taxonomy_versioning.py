from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, TaxonomyLabel, TaxonomyVersion
from app.taxonomy import TaxonomyDimension, TaxonomyInvariantError, TaxonomyVersionStatus


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_published_taxonomy_and_its_labels_are_immutable(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        version = TaxonomyVersion(id=uuid.uuid4(), version="v1", checksum="f" * 64)
        label = TaxonomyLabel(
            taxonomy_version=version,
            dimension=TaxonomyDimension.CONTENT,
            slug="education-reference",
            display_name="Education & Reference",
        )
        session.add_all([version, label])
        await session.commit()

        version.status = TaxonomyVersionStatus.PUBLISHED
        await session.commit()

        version_id = version.id
        version.change_notes = "must not be changed"
        with pytest.raises(TaxonomyInvariantError, match="published_taxonomy_version_is_immutable"):
            await session.flush()
        await session.rollback()

    async with maker() as session:
        version = await session.get(TaxonomyVersion, version_id)
        assert version is not None
        session.add(
            TaxonomyLabel(
                taxonomy_version_id=version.id,
                dimension=TaxonomyDimension.CONTENT,
                slug="science-research",
                display_name="Science & Research",
            )
        )
        with pytest.raises(TaxonomyInvariantError, match="published_taxonomy_labels_are_immutable"):
            await session.flush()


@pytest.mark.asyncio
async def test_taxonomy_corrections_use_a_successor_version(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        published = TaxonomyVersion(
            id=uuid.uuid4(),
            version="v1",
            checksum="1" * 64,
            status=TaxonomyVersionStatus.PUBLISHED,
        )
        successor = TaxonomyVersion(
            id=uuid.uuid4(),
            version="v2",
            checksum="2" * 64,
            parent_version=published,
        )
        session.add_all([published, successor])
        await session.commit()

        assert successor.parent_version_id == published.id
        assert successor.status is TaxonomyVersionStatus.DRAFT
