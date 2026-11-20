"""Version 1 canonical HMAC-SHA256 request signing."""

import hashlib
import hmac
import re
import time
from urllib.parse import parse_qsl, quote

_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z", re.ASCII)
_NONCE = re.compile(r"[A-Za-z0-9_-]{16,128}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_query(raw_query: str) -> str:
    """Normalize query pairs by percent-encoding and sorting key/value pairs."""
    if not raw_query:
        return ""
    pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True)
    encoded = [(quote(k, safe="-._~"), quote(v, safe="-._~")) for k, v in pairs]
    return "&".join(f"{key}={value}" for key, value in sorted(encoded))


def canonical_request(
    method: str,
    path: str,
    query: str,
    device_id: str,
    key_id: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> bytes:
    if not re.fullmatch(r"[A-Z]+", method, re.ASCII):
        raise ValueError("Método inválido")
    if not path.startswith("/") or "\n" in path or "\r" in path:
        raise ValueError("Ruta inválida")
    if not _IDENTIFIER.fullmatch(device_id) or not _IDENTIFIER.fullmatch(key_id):
        raise ValueError("Identificador inválido")
    if not _NONCE.fullmatch(nonce):
        raise ValueError("Nonce inválido")
    if not re.fullmatch(r"[0-9]{1,12}", timestamp, re.ASCII):
        raise ValueError("Timestamp inválido")
    if not _SHA256.fullmatch(body_sha256):
        raise ValueError("Hash inválido")
    return "\n".join((method, path, canonical_query(query), device_id, key_id, timestamp, nonce, body_sha256)).encode("utf-8")


def sign(secret: bytes | str, message: bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify(secret: bytes | str, message: bytes, signature: str) -> bool:
    return bool(_SHA256.fullmatch(signature)) and hmac.compare_digest(sign(secret, message), signature)


def timestamp_is_fresh(timestamp: str, tolerance: int, now: float | None = None) -> bool:
    if not re.fullmatch(r"[0-9]{1,12}", timestamp, re.ASCII):
        return False
    return abs((time.time() if now is None else now) - int(timestamp)) <= tolerance

