import hashlib
import time
import uuid

from fastapi.testclient import TestClient

import app.api.v1.mildiu as routes
from app.security.auth_service import Principal
from app.security.hmac_auth import canonical_request, sign


def headers(method, path, body, nonce=None):
    timestamp = str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    digest = hashlib.sha256(body).hexdigest()
    message = canonical_request(method, path, "", "PROT-001", "key-001", timestamp, nonce, digest)
    return {
        "X-Device-ID": "PROT-001",
        "X-Key-ID": "key-001",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Content-SHA256": digest,
        "X-Signature": sign("x" * 40, message),
    }


def test_take_route_authenticates_exact_bytes(monkeypatch):
    from app.main import app

    body = b'{"not":"validated until authenticated"}'
    seen = {}

    def fake_auth(_settings, **kwargs):
        seen.update(kwargs)
        return Principal(1, "PROT-001", "key-001")

    monkeypatch.setattr(routes, "authenticate", fake_auth)
    client = TestClient(app)
    response = client.post(
        "/api/v1/mildiu/tomas",
        content=body,
        headers=headers("POST", "/api/v1/mildiu/tomas", body) | {"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert seen["body"] == body


def test_missing_auth_headers_are_rejected(monkeypatch):
    from app.main import app

    routes._rates.clear()
    client = TestClient(app)
    response = client.post("/api/v1/mildiu/tomas", content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


def test_multipart_route_uses_the_materialized_exact_body(monkeypatch):
    from app.main import app

    routes._rates.clear()
    toma_id, surco_id = uuid.uuid4(), uuid.uuid4()
    image_id, lote_id = uuid.uuid4(), uuid.uuid4()
    field = f"imagen_{image_id.hex}"
    metadata = {
        "client_lote_id": str(lote_id),
        "imagenes": [
            {
                "client_imagen_id": str(image_id),
                "campo_archivo": field,
                "categoria": "con_mildiu",
            }
        ],
    }
    seen = {}

    def fake_auth(_settings, **kwargs):
        seen["body"] = kwargs["body"]
        return Principal(1, "PROT-001", "key-001")

    def fake_stage(_settings, parsed, uploads):
        assert parsed.client_lote_id == lote_id
        assert list(uploads) == [field]
        return "storage", ["prepared"]

    def fake_persist(*_args):
        return {
            "lote_id": str(uuid.uuid4()),
            "client_lote_id": str(lote_id),
            "estado": "completado",
            "idempotent_replay": False,
            "imagenes_duplicadas": [],
        }

    monkeypatch.setattr(routes, "authenticate", fake_auth)
    monkeypatch.setattr(routes, "stage_batch", fake_stage)
    monkeypatch.setattr(routes, "persist_batch", fake_persist)
    path = f"/api/v1/mildiu/tomas/{toma_id}/surcos/{surco_id}/imagenes"
    client = TestClient(app)
    request = client.build_request(
        "POST",
        path,
        data={"metadata": __import__("json").dumps(metadata)},
        files=[(field, ("leaf.jpg", b"jpeg bytes", "image/jpeg"))],
    )
    raw = request.read()
    request.headers.update(headers("POST", path, raw))
    response = client.send(request)
    assert response.status_code == 201, response.text
    assert seen["body"] == raw

