from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import delete, select

from app.config import Settings, get_settings
from app.dependencies import Csrf, CurrentUser, Db
from app.models import Session, User
from app.schemas import LoginRequest, UserResponse
from app.security import hash_token, new_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(user.password_hash, payload.password)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token, csrf = new_token(), new_token()
    expires = datetime.now(UTC) + timedelta(minutes=settings.session_ttl_minutes)
    db.add(
        Session(
            token_hash=hash_token(token, settings.secret_key),
            user_id=user.id,
            expires_at=expires,
        )
    )
    await db.commit()
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        max_age=settings.session_ttl_minutes * 60,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        "csrf_token",
        csrf,
        httponly=False,
        max_age=settings.session_ttl_minutes * 60,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    _: Csrf,
    user: CurrentUser,
    db: Db,
    settings: Annotated[Settings, Depends(get_settings)],
    session_token: Annotated[str | None, Cookie()] = None,
) -> None:
    del user
    if session_token:
        await db.execute(
            delete(Session).where(
                Session.token_hash == hash_token(session_token, settings.secret_key)
            )
        )
        await db.commit()
    response.delete_cookie("session_token", path="/")
    response.delete_cookie("csrf_token", path="/")


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> User:
    return user
