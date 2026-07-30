import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user
from app.main import app
from app.models import Base, Role, User, Website


@pytest.fixture
async def api_db() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], User, User, Website]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        admin = User(
            email=f"{uuid.uuid4()}@example.com", password_hash=uuid.uuid4().hex, role=Role.ADMIN
        )
        viewer = User(
            email=f"{uuid.uuid4()}@example.com", password_hash=uuid.uuid4().hex, role=Role.VIEWER
        )
        website = Website(
            domain="ai-api.example.com",
            registrable_domain="example.com",
            canonical_url="https://ai-api.example.com",
        )
        db.add_all([admin, viewer, website])
        await db.commit()

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = database
    yield maker, admin, viewer, website
    app.dependency_overrides.clear()
    await engine.dispose()


async def post(path: str, user: User | None, *, csrf: bool = True) -> httpx.Response:
    if user:
        app.dependency_overrides[current_user] = lambda: user
    else:
        app.dependency_overrides.pop(current_user, None)
    headers = {"X-CSRF-Token": "token"} if csrf else {}
    cookies = {"csrf_token": "token"} if csrf else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        return await client.post(path, json={"trigger": "admin"}, headers=headers, cookies=cookies)


async def test_ai_request_requires_auth_admin_and_csrf(
    api_db: tuple[async_sessionmaker[AsyncSession], User, User, Website],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, admin, viewer, website = api_db
    settings = get_settings().model_copy(
        update={"ai_enabled": True, "ai_provider": "fake", "ai_model": "fake"}
    )
    monkeypatch.setattr("app.routers.ai.get_settings", lambda: settings)

    async def no_rate(*_: object) -> None:
        pass

    async def no_dispatch(*_: object) -> None:
        pass

    monkeypatch.setattr("app.routers.ai.enforce_ai_enqueue_rate", no_rate)
    monkeypatch.setattr("app.routers.ai.dispatch_ai", no_dispatch)
    path = f"/api/ai/websites/{website.id}/classify"
    assert (await post(path, None)).status_code == 401
    assert (await post(path, viewer)).status_code == 403
    assert (await post(path, admin, csrf=False)).status_code == 403
    response = await post(path, admin)
    assert response.status_code == 202
    body = response.json()
    assert "api_key" not in str(body).lower()
    assert body["ai"]["provider"] == "fake"


async def test_disabled_mode_fails_closed(
    api_db: tuple[async_sessionmaker[AsyncSession], User, User, Website],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, admin, _, website = api_db
    monkeypatch.setattr("app.routers.ai.get_settings", get_settings)
    response = await post(f"/api/ai/websites/{website.id}/classify", admin)
    assert response.status_code == 503
    assert "key" not in response.text.lower()
