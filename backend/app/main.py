import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.logging import configure_logging, request_id
from app.routers import admin, auth

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("application_started")
    yield
    await engine.dispose()


app = FastAPI(title="WebAge Classifier API", version="0.1.0", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_identifier = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    token = request_id.set(request_identifier)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed", extra={"method": request.method, "path": request.url.path}
        )
        raise
    finally:
        request_id.reset(token)
    response.headers["X-Request-ID"] = request_identifier
    logger.info(
        "request_complete",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        },
    )
    return response


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready", tags=["operations"])
async def ready() -> JSONResponse:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness_check_failed")
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})


app.include_router(auth.router)
app.include_router(admin.router)
