"""Database-backed HMAC authentication, replay protection, and key rotation."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import ApiError
from app.database.connection import get_session_factory
from app.models.mildiu import CredencialPrototipoMildiu, NonceMildiu, PrototipoMildiu
from app.security.hmac_auth import canonical_request, sha256_hex, timestamp_is_fresh, verify
from app.security.secrets import SecretCipher

log = logging.getLogger("quinuaalert.auth")


@dataclass(frozen=True)
class Principal:
    prototipo_id: int
    device_id: str
    key_id: str


def authenticate(
    settings: Settings,
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    device_id: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    content_sha256: str,
    signature: str,
) -> Principal:
    """Authenticate and consume a nonce atomically in PostgreSQL."""
    actual_digest = sha256_hex(body)
    if actual_digest != content_sha256 or not timestamp_is_fresh(timestamp, settings.hmac_timestamp_tolerance):
        log.warning("Authentication rejected: stale timestamp or body digest mismatch")
        raise ApiError(401, "authentication_failed", "Autenticación inválida")
    try:
        message = canonical_request(method, path, query, device_id, key_id, timestamp, nonce, content_sha256)
    except ValueError as exc:
        log.warning("Authentication rejected: malformed signed metadata")
        raise ApiError(401, "authentication_failed", "Autenticación inválida") from exc

    now = datetime.now(timezone.utc)
    try:
        with get_session_factory().begin() as session:
            row = session.execute(
                select(PrototipoMildiu, CredencialPrototipoMildiu)
                .join(CredencialPrototipoMildiu, CredencialPrototipoMildiu.prototipo_id == PrototipoMildiu.prototipo_id)
                .where(PrototipoMildiu.codigo == device_id, CredencialPrototipoMildiu.key_id == key_id)
                .with_for_update(of=PrototipoMildiu)
            ).first()
            if row is None:
                log.warning("Authentication rejected: unknown device or key")
                raise ApiError(401, "authentication_failed", "Autenticación inválida")
            prototype, credential = row
            if prototype.estado != "activo":
                log.warning("Authentication rejected: device state=%s", prototype.estado)
                raise ApiError(403, "device_disabled", "Prototipo no autorizado")
            credential_valid = (
                credential.estado == "activa"
                and credential.valida_desde <= now
                and (credential.valida_hasta is None or credential.valida_hasta > now)
            )
            if not credential_valid:
                log.warning("Authentication rejected: credential inactive")
                raise ApiError(403, "credential_inactive", "Credencial no autorizada")
            try:
                secret = SecretCipher(settings.hmac_master_key.get_secret_value()).decrypt(credential.secreto_cifrado)
            except ValueError as exc:
                log.error("Credential decryption failed for device_id=%s key_id=%s", device_id, key_id)
                raise ApiError(500, "credential_unavailable", "Error interno del servidor") from exc
            if not verify(secret, message, signature):
                log.warning("Authentication rejected: invalid signature")
                raise ApiError(401, "authentication_failed", "Autenticación inválida")

            cutoff = now - timedelta(minutes=1)
            recent = session.scalar(
                select(func.count()).select_from(NonceMildiu).where(
                    NonceMildiu.prototipo_id == prototype.prototipo_id,
                    NonceMildiu.creado_en >= cutoff,
                )
            )
            if recent >= settings.rate_limit_per_minute:
                raise ApiError(429, "rate_limit_exceeded", "Límite de solicitudes excedido")

            session.execute(
                delete(NonceMildiu).where(
                    NonceMildiu.prototipo_id == prototype.prototipo_id,
                    NonceMildiu.expira_en < now,
                )
            )
            statement = (
                insert(NonceMildiu)
                .values(
                    prototipo_id=prototype.prototipo_id,
                    credencial_id=credential.credencial_id,
                    nonce=nonce,
                    timestamp_solicitud=int(timestamp),
                    expira_en=now + timedelta(seconds=settings.nonce_ttl),
                )
                .on_conflict_do_nothing(constraint="uq_nonce_mildiu_credencial")
                .returning(NonceMildiu.nonce_id)
            )
            if session.execute(statement).scalar_one_or_none() is None:
                raise ApiError(409, "nonce_reused", "Nonce ya utilizado")
            if not timestamp_is_fresh(timestamp, settings.hmac_timestamp_tolerance):
                raise ApiError(401, "authentication_failed", "Autenticación inválida")
            prototype.ultima_comunicacion = now
            return Principal(prototype.prototipo_id, prototype.codigo, credential.key_id)
    except ApiError:
        raise
    except SQLAlchemyError as exc:
        log.error("Authentication database failure")
        raise ApiError(503, "database_unavailable", "Base de datos temporalmente no disponible") from exc


def cleanup_expired_nonces() -> int:
    """Delete expired replay records; safe when multiple workers run it."""
    now = datetime.now(timezone.utc)
    try:
        with get_session_factory().begin() as session:
            result = session.execute(delete(NonceMildiu).where(NonceMildiu.expira_en < now))
            return result.rowcount or 0
    except SQLAlchemyError:
        log.error("Expired nonce cleanup failed")
        return 0

