"""Authenticated REST boundary for the mildew acquisition workflow."""

import json
import logging
import asyncio
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.core.errors import ApiError
from app.schemas.mildiu import TomaCreate, UploadMetadata
from app.security.auth_service import authenticate
from app.services.mildiu_service import finalize_take, get_take_details, get_take_status, register_take
from app.services.upload_service import persist_batch, stage_batch

router = APIRouter(prefix="/api/v1/mildiu", tags=["mildiu"])
log = logging.getLogger("quinuaalert.mildiu.http")

_AUTH_HEADERS = (
    "x-device-id",
    "x-key-id",
    "x-timestamp",
    "x-nonce",
    "x-content-sha256",
    "x-signature",
)
_rate_lock = threading.Lock()
_rates: dict[str, deque[float]] = defaultdict(deque)


async def _bounded_body(request: Request, maximum: int, timeout_seconds: int) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > maximum:
                raise ApiError(413, "request_too_large", "Cuerpo de solicitud demasiado grande")
        except ValueError as exc:
            raise ApiError(400, "invalid_content_length", "Content-Length inválido") from exc
    chunks: list[bytes] = []
    size = 0
    try:
        async with asyncio.timeout(timeout_seconds):
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise ApiError(413, "request_too_large", "Cuerpo de solicitud demasiado grande")
                chunks.append(chunk)
    except TimeoutError as exc:
        raise ApiError(408, "request_timeout", "La carga excedió el tiempo máximo") from exc
    body = b"".join(chunks)
    request._body = body
    return body


def _rate_limit(request: Request, limit: int) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    cutoff = now - 60
    with _rate_lock:
        entries = _rates[key]
        while entries and entries[0] <= cutoff:
            entries.popleft()
        if len(entries) >= limit:
            raise ApiError(429, "rate_limit_exceeded", "Límite de solicitudes excedido")
        entries.append(now)
        if len(_rates) > 10_000:
            for candidate in list(_rates):
                if not _rates[candidate] or _rates[candidate][-1] <= cutoff:
                    del _rates[candidate]


async def _authenticate_request(request: Request, body: bytes):
    settings = get_settings()
    if settings.enforce_https and request.url.scheme != "https":
        raise ApiError(400, "https_required", "Se requiere HTTPS")
    _rate_limit(request, max(settings.rate_limit_per_minute * 2, 10))
    values = [request.headers.getlist(name) for name in _AUTH_HEADERS]
    if any(len(value) != 1 for value in values):
        log.warning("Authentication rejected: missing or duplicated headers")
        raise ApiError(401, "authentication_failed", "Autenticación inválida")
    device, key_id, timestamp, nonce, content_hash, signature = (value[0] for value in values)
    return await run_in_threadpool(
        authenticate,
        settings,
        method=request.method,
        path=request.url.path,
        query=request.url.query,
        body=body,
        device_id=device,
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        content_sha256=content_hash,
        signature=signature,
    )


def _require_content_type(request: Request, expected: str) -> None:
    actual = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if actual != expected:
        raise ApiError(415, "unsupported_media_type", f"Se requiere {expected}")


def _uuid(value: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ApiError(422, "invalid_identifier", f"{name} inválido") from exc


@router.post("/tomas", summary="Registrar una toma y sus surcos")
async def create_take(request: Request):
    settings = get_settings()
    _require_content_type(request, "application/json")
    body = await _bounded_body(request, settings.max_metadata_request_bytes, settings.request_body_timeout_seconds)
    principal = await _authenticate_request(request, body)
    try:
        payload = TomaCreate.model_validate_json(body)
    except ValidationError as exc:
        raise ApiError(422, "invalid_take", "Los datos de la toma son inválidos") from exc
    data = await run_in_threadpool(register_take, settings, principal, payload)
    status = 200 if data["idempotent_replay"] else 201
    return JSONResponse(status_code=status, content={"success": True, "data": data})


@router.post("/tomas/{toma_id}/surcos/{surco_id}/imagenes", summary="Subir un lote de imágenes")
async def upload_images(toma_id: str, surco_id: str, request: Request):
    settings = get_settings()
    _require_content_type(request, "multipart/form-data")
    overhead = settings.max_metadata_request_bytes + settings.max_files_per_batch * 4096
    body = await _bounded_body(request, settings.max_upload_total_bytes + overhead, settings.request_body_timeout_seconds)
    principal = await _authenticate_request(request, body)
    try:
        form = await request.form(
            max_files=settings.max_files_per_batch,
            max_fields=settings.max_files_per_batch + 1,
        )
    except Exception as exc:
        raise ApiError(422, "invalid_multipart", "Formulario multipart inválido") from exc
    metadata_values = form.getlist("metadata")
    if len(metadata_values) != 1 or not isinstance(metadata_values[0], str):
        raise ApiError(422, "invalid_metadata", "Se requiere un único campo metadata JSON")
    try:
        metadata = UploadMetadata.model_validate_json(metadata_values[0])
    except ValidationError as exc:
        raise ApiError(422, "invalid_metadata", "metadata no cumple el contrato") from exc
    uploads: dict[str, UploadFile] = {}
    for field, value in form.multi_items():
        if field == "metadata":
            continue
        if not isinstance(value, UploadFile) or field in uploads:
            raise ApiError(422, "invalid_file_part", "Parte de archivo inválida o duplicada")
        uploads[field] = value
    storage, prepared = await run_in_threadpool(stage_batch, settings, metadata, uploads)
    data = await run_in_threadpool(
        persist_batch,
        settings,
        principal,
        _uuid(toma_id, "toma_id"),
        _uuid(surco_id, "surco_id"),
        metadata,
        storage,
        prepared,
    )
    status = 200 if data["idempotent_replay"] else 201
    return JSONResponse(status_code=status, content={"success": True, "data": data})


@router.post("/tomas/{toma_id}/finalizar", summary="Validar y finalizar una toma")
async def finish_take(toma_id: str, request: Request):
    settings = get_settings()
    body = await _bounded_body(request, settings.max_metadata_request_bytes, settings.request_body_timeout_seconds)
    if body:
        raise ApiError(422, "body_not_allowed", "Este endpoint requiere un cuerpo vacío")
    principal = await _authenticate_request(request, body)
    data = await run_in_threadpool(finalize_take, principal, _uuid(toma_id, "toma_id"))
    return {"success": True, "data": data}


@router.get("/tomas/{toma_id}", summary="Consultar una toma")
async def read_take(toma_id: str, request: Request):
    settings = get_settings()
    body = await _bounded_body(request, settings.max_metadata_request_bytes, settings.request_body_timeout_seconds)
    if body:
        raise ApiError(422, "body_not_allowed", "Este endpoint requiere un cuerpo vacío")
    principal = await _authenticate_request(request, body)
    data = await run_in_threadpool(get_take_details, principal, _uuid(toma_id, "toma_id"))
    return {"success": True, "data": data}


@router.get("/tomas/{toma_id}/estado", summary="Consultar el avance y reanudación")
async def read_take_status(toma_id: str, request: Request):
    settings = get_settings()
    body = await _bounded_body(request, settings.max_metadata_request_bytes, settings.request_body_timeout_seconds)
    if body:
        raise ApiError(422, "body_not_allowed", "Este endpoint requiere un cuerpo vacío")
    principal = await _authenticate_request(request, body)
    data = await run_in_threadpool(get_take_status, principal, _uuid(toma_id, "toma_id"))
    return {"success": True, "data": data}

