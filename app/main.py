"""FastAPI application entrypoint."""

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api.v1.mildiu import router as mildiu_router
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import configure_logging
from app.database.connection import close_database, database_ready, get_engine
from app.services.upload_service import make_storage, recover_pending_uploads
from app.security.auth_service import cleanup_expired_nonces

log = logging.getLogger("quinuaalert")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    get_engine()
    make_storage(settings)
    recovered = await run_in_threadpool(recover_pending_uploads, settings)
    if recovered:
        log.info("Reconciled pending image batches count=%s", recovered)
    stop_cleanup = asyncio.Event()

    async def nonce_cleanup_loop() -> None:
        while not stop_cleanup.is_set():
            await run_in_threadpool(cleanup_expired_nonces)
            try:
                await asyncio.wait_for(stop_cleanup.wait(), timeout=settings.nonce_cleanup_interval)
            except TimeoutError:
                continue

    cleanup_task = asyncio.create_task(nonce_cleanup_loop())
    try:
        yield
    finally:
        stop_cleanup.set()
        await cleanup_task
        close_database()


app = FastAPI(
    title="QuinuAlert API",
    version="2.0.0",
    description="Recepción autenticada de tomas e imágenes para detección de mildiu.",
    lifespan=lifespan,
    debug=False,
)
app.include_router(mildiu_router)


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/")
def index():
    return {"service": "QuinuAlert API", "version": "2.0.0", "documentation": "/docs"}


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        if database_ready():
            return {"status": "ok"}
    except SQLAlchemyError:
        log.error("Database readiness check failed")
    return JSONResponse(status_code=503, content={"status": "unavailable"})

