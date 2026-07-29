import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.artifacts import PNG_SIGNATURE, ArtifactError, ArtifactStore
from app.browser_tasks import _cleanup
from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user
from app.main import app
from app.models import (
    Base,
    BrowserInspection,
    BrowserStatus,
    ClassificationRun,
    QueueName,
    Role,
    User,
    Website,
)


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
async def artifact_database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def seed_artifact(
    maker: async_sessionmaker[AsyncSession], root: Path, *, expired: bool = False
) -> tuple[str, User, User, uuid.UUID]:
    stored = ArtifactStore(str(root), 1024).put_png(png())
    async with maker() as db:
        owner = User(
            email=f"owner-{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.VIEWER,
        )
        unrelated = User(
            email=f"other-{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.VIEWER,
        )
        website = Website(
            domain=f"{uuid.uuid4()}.example.com",
            registrable_domain="example.com",
            canonical_url="https://example.com/",
        )
        db.add_all([owner, unrelated, website])
        await db.flush()
        run = ClassificationRun(
            website_id=website.id,
            requested_by_id=owner.id,
            queue_name=QueueName.BROWSER,
            task_id=str(uuid.uuid4()),
        )
        db.add(run)
        await db.flush()
        inspection = BrowserInspection(
            run_id=run.id,
            status=BrowserStatus.COMPLETED,
            trigger="test",
            artifact_id=stored.artifact_id,
            artifact_expires_at=datetime.now(UTC)
            + (-timedelta(minutes=1) if expired else timedelta(hours=1)),
        )
        db.add(inspection)
        await db.commit()
        return stored.artifact_id, owner, unrelated, inspection.id


def install_database(maker: async_sessionmaker[AsyncSession]) -> None:
    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = database


async def request_as(user: User | None, path: str) -> httpx.Response:
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    else:
        app.dependency_overrides.pop(current_user, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        return await client.get(path)


async def test_screenshot_access_is_authenticated_authorized_and_path_safe(
    artifact_database: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_id, owner, unrelated, _ = await seed_artifact(artifact_database, tmp_path)
    install_database(artifact_database)
    settings = get_settings().model_copy(update={"browser_artifact_root": str(tmp_path)})
    monkeypatch.setattr("app.routers.browser.get_settings", lambda: settings)

    assert (await request_as(None, f"/api/browser/artifacts/{artifact_id}")).status_code == 401
    assert (await request_as(unrelated, f"/api/browser/artifacts/{artifact_id}")).status_code == 404
    response = await request_as(owner, f"/api/browser/artifacts/{artifact_id}")
    assert response.status_code == 200
    assert response.content == png()
    assert str(tmp_path) not in str(response.headers)


@pytest.mark.parametrize(
    "attack",
    [
        "../etc/passwd",
        "%2e%2e%2fetc%2fpasswd",
        "%2Fetc%2Fpasswd",
        "..%5C..%5Csecret",
        "not-an-opaque-id",
        "a%00b",
    ],
)
async def test_screenshot_identifier_attacks_are_rejected(
    artifact_database: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    _, owner, _, _ = await seed_artifact(artifact_database, tmp_path)
    install_database(artifact_database)
    settings = get_settings().model_copy(update={"browser_artifact_root": str(tmp_path)})
    monkeypatch.setattr("app.routers.browser.get_settings", lambda: settings)
    response = await request_as(owner, f"/api/browser/artifacts/{attack}")
    assert response.status_code in {400, 404, 422}
    assert str(tmp_path) not in response.text


async def test_expired_screenshot_is_hidden_and_cleanup_removes_state_and_file(
    artifact_database: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_id, owner, _, inspection_id = await seed_artifact(
        artifact_database, tmp_path, expired=True
    )
    install_database(artifact_database)
    settings = get_settings().model_copy(
        update={
            "browser_artifact_root": str(tmp_path),
            "browser_screenshot_max_bytes": 1024,
        }
    )
    monkeypatch.setattr("app.routers.browser.get_settings", lambda: settings)
    monkeypatch.setattr("app.browser_tasks.settings", settings)
    monkeypatch.setattr("app.browser_tasks.SessionLocal", artifact_database)
    assert (await request_as(owner, f"/api/browser/artifacts/{artifact_id}")).status_code == 404
    assert await _cleanup() == 1
    assert not (tmp_path / f"{artifact_id}.png").exists()
    async with artifact_database() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        assert inspection.artifact_id is None
        assert inspection.artifact_expires_at is None


def test_screenshot_content_type_and_size_validation(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), len(png()))
    with pytest.raises(ArtifactError, match="invalid_image_type"):
        store.put_png(b"<svg onload=attack()>")
    with pytest.raises(ArtifactError, match="artifact_too_large"):
        ArtifactStore(str(tmp_path), len(png()) - 1).put_png(png())
    stored = store.put_png(png())
    assert store.path_for_read(stored.artifact_id).read_bytes() == png()
