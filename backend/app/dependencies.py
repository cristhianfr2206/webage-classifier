from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Role, Session, User
from app.security import hash_token

Db = Annotated[AsyncSession, Depends(get_db)]


async def current_user(
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Cookie()] = None,
) -> User:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    result = await db.execute(
        select(Session)
        .where(
            Session.token_hash == hash_token(session_token, settings.secret_key),
            Session.expires_at > datetime.now(UTC),
        )
        .options()
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inactive account")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def csrf_protect(
    request: Request,
    csrf_cookie: Annotated[str | None, Cookie(alias="csrf_token")] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not csrf_cookie or not csrf_header or not secrets_compare(csrf_cookie, csrf_header):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed")


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


Csrf = Annotated[None, Depends(csrf_protect)]
