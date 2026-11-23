"""Ejecuta de principio a fin una prueba del servicio web de mildiu.

El script usa ``toma_prueba_manual.json`` como plantilla, genera todos los UUID
del cliente, carga las imagenes configuradas, finaliza la toma y consulta el
resultado. El secreto HMAC se obtiene exclusivamente de variables de entorno.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas.mildiu import TomaCreate
from scripts.raspberry_client import SignedClient

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PAYLOAD = SCRIPT_DIR / "toma_prueba_manual.json"
VALID_CATEGORIES = {"con_mildiu", "sin_mildiu", "posible_mildiu"}

# Nombres usados por la prueba manual. Para usar otra carpeta, ejecute con
# --image-dir o defina QUINUALERT_IMAGE_DIR. Tambien puede editar esta lista.
DEFAULT_IMAGE_FILES = (
    ("con_mildiu", "mildiu_01.jpg"),
    ("con_mildiu", "mildiu_02.jpg"),
    ("con_mildiu", "mildiu_03.jpg"),
    ("sin_mildiu", "sana_01.jpg"),
    ("sin_mildiu", "sana_02.jpg"),
    ("sin_mildiu", "sana_03.jpg"),
    ("posible_mildiu", "posible_01.jpg"),
)


class WorkflowError(RuntimeError):
    """Error esperado y legible durante la prueba integral."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Registra una toma, sube imagenes, finaliza y consulta el resultado."
    )
    result.add_argument(
        "--payload",
        type=Path,
        default=DEFAULT_PAYLOAD,
        help="Plantilla JSON de la toma.",
    )
    result.add_argument(
        "--image-dir",
        type=Path,
        default=Path(
            os.getenv("QUINUALERT_IMAGE_DIR", str(Path.home() / "Documents"))
        ),
        help="Carpeta que contiene las siete imagenes configuradas.",
    )
    result.add_argument(
        "--numero-toma-semana",
        type=int,
        choices=range(1, 21),
        metavar="1..20",
        help="Reemplaza numero_toma_semana de la plantilla.",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida plantilla e imagenes sin enviar solicitudes.",
    )
    return result


def resolve_images(image_dir: Path) -> list[tuple[str, Path]]:
    resolved: list[tuple[str, Path]] = []
    errors: list[str] = []
    total_bytes = 0
    for category, filename in DEFAULT_IMAGE_FILES:
        if category not in VALID_CATEGORIES:
            errors.append(f"Categoria no permitida: {category}")
            continue
        path = (image_dir / filename).expanduser().resolve()
        if not path.is_file():
            errors.append(f"No existe: {path}")
            continue
        size = path.stat().st_size
        if size <= 0:
            errors.append(f"El archivo esta vacio: {path}")
            continue
        total_bytes += size
        resolved.append((category, path))
    if errors:
        raise WorkflowError("\n".join(errors))
    if total_bytes > 50 * 1024 * 1024:
        raise WorkflowError("Las imagenes superan el limite total de 50 MB")
    return resolved


def build_payload(
    path: Path,
    images: list[tuple[str, Path]],
    weekly_number: int | None,
) -> tuple[bytes, TomaCreate]:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"No se pudo leer la plantilla {path}: {exc}") from exc

    surcos = raw.get("surcos")
    if not isinstance(surcos, list) or len(surcos) != 1:
        raise WorkflowError("La prueba automatica requiere exactamente un surco")

    counts = Counter(category for category, _path in images)
    processed = len(images)
    raw["client_toma_id"] = str(uuid.uuid4())
    if weekly_number is not None:
        raw["numero_toma_semana"] = weekly_number
    surcos[0]["resultados"] = {
        "total_imagenes": processed,
        "imagenes_procesadas": processed,
        "imagenes_con_mildiu": counts["con_mildiu"],
        "imagenes_sin_mildiu": counts["sin_mildiu"],
        "imagenes_posible_mildiu": counts["posible_mildiu"],
        "imagenes_no_procesadas": 0,
    }

    try:
        payload = TomaCreate.model_validate(raw)
    except Exception as exc:
        raise WorkflowError(f"La plantilla no cumple el contrato: {exc}") from exc
    body = json.dumps(
        payload.model_dump(mode="json", exclude_none=False),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return body, payload


def show_response(label: str, response: httpx.Response) -> dict[str, Any]:
    print(f"\n--- {label} ---")
    print(f"HTTP {response.status_code}")
    try:
        document = response.json()
        print(json.dumps(document, indent=2, ensure_ascii=False))
    except ValueError as exc:
        raise WorkflowError(f"Respuesta no JSON: {response.text}") from exc
    if not response.is_success:
        error = document.get("error", {}) if isinstance(document, dict) else {}
        code = error.get("code", "http_error")
        message = error.get("message", "Solicitud rechazada")
        raise WorkflowError(f"{code}: {message}")
    if not isinstance(document, dict) or not document.get("success"):
        raise WorkflowError("La respuesta exitosa no tiene el formato esperado")
    data = document.get("data")
    if not isinstance(data, dict):
        raise WorkflowError("La respuesta no contiene data")
    return data


def upload_images(
    client: SignedClient,
    toma_id: str,
    surco_id: str,
    images: list[tuple[str, Path]],
) -> dict[str, Any]:
    batch_id = uuid.uuid4()
    metadata_images: list[dict[str, str]] = []
    files: list[tuple[str, tuple[str, Any, str]]] = []
    handles = []
    try:
        for category, path in images:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            image_id = uuid.uuid5(batch_id, f"{path}:{digest}")
            field = f"imagen_{image_id.hex}"
            metadata_images.append(
                {
                    "client_imagen_id": str(image_id),
                    "campo_archivo": field,
                    "categoria": category,
                    "hash_sha256": digest,
                }
            )
            handle = path.open("rb")
            handles.append(handle)
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            files.append((field, (path.name, handle, mime)))

        metadata = json.dumps(
            {"client_lote_id": str(batch_id), "imagenes": metadata_images},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        response = client.send(
            "POST",
            f"/api/v1/mildiu/tomas/{toma_id}/surcos/{surco_id}/imagenes",
            data={"metadata": metadata},
            files=files,
        )
        return show_response("CARGA DE IMAGENES", response)
    finally:
        for handle in handles:
            handle.close()


def required_environment() -> tuple[str, str, str, str]:
    base_url = os.getenv("QUINUALERT_BASE_URL", "http://127.0.0.1:8000")
    values = {
        "QUINUALERT_DEVICE_ID": os.getenv("QUINUALERT_DEVICE_ID"),
        "QUINUALERT_KEY_ID": os.getenv("QUINUALERT_KEY_ID"),
        "QUINUALERT_DEVICE_SECRET": os.getenv("QUINUALERT_DEVICE_SECRET"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise WorkflowError(
            "Faltan variables de entorno: " + ", ".join(missing)
        )
    return base_url, values["QUINUALERT_DEVICE_ID"], values["QUINUALERT_KEY_ID"], values["QUINUALERT_DEVICE_SECRET"]


def run(args: argparse.Namespace) -> None:
    images = resolve_images(args.image_dir)
    body, payload = build_payload(args.payload, images, args.numero_toma_semana)
    counts = Counter(category for category, _path in images)
    print("Configuracion validada")
    print(f"  client_toma_id: {payload.client_toma_id}")
    print(f"  numero_toma_semana: {payload.numero_toma_semana}")
    print(f"  carpeta de imagenes: {args.image_dir.resolve()}")
    print(
        "  imagenes: "
        f"{counts['con_mildiu']} con_mildiu, "
        f"{counts['sin_mildiu']} sin_mildiu, "
        f"{counts['posible_mildiu']} posible_mildiu"
    )
    if args.dry_run:
        print("DRY RUN: no se enviaron solicitudes")
        return

    base_url, device_id, key_id, secret = required_environment()
    try:
        health = httpx.get(
            f"{base_url.rstrip('/')}/health/ready", timeout=10, verify=True
        )
    except httpx.HTTPError as exc:
        raise WorkflowError(f"No se pudo conectar con la API: {exc}") from exc
    if health.status_code != 200:
        raise WorkflowError(
            f"La API no esta lista: HTTP {health.status_code} {health.text}"
        )
    print(f"API disponible en {base_url}")

    client = SignedClient(base_url, device_id, key_id, secret)
    try:
        registration = show_response(
            "REGISTRO DE TOMA",
            client.send(
                "POST",
                "/api/v1/mildiu/tomas",
                content=body,
                headers={"Content-Type": "application/json"},
            ),
        )
        toma_id = str(registration["toma_id"])
        surcos = registration.get("surcos")
        if not isinstance(surcos, list) or len(surcos) != 1:
            raise WorkflowError("El registro no devolvio exactamente un surco")
        surco_id = str(surcos[0]["surco_id"])

        upload_images(client, toma_id, surco_id, images)
        show_response(
            "ESTADO",
            client.send(
                "GET", f"/api/v1/mildiu/tomas/{toma_id}/estado", content=b""
            ),
        )
        show_response(
            "FINALIZACION",
            client.send(
                "POST", f"/api/v1/mildiu/tomas/{toma_id}/finalizar", content=b""
            ),
        )
        detail = show_response(
            "DETALLE FINAL",
            client.send("GET", f"/api/v1/mildiu/tomas/{toma_id}", content=b""),
        )
        print("\nPRUEBA COMPLETADA")
        print(f"  toma_id: {toma_id}")
        print(f"  surco_id: {surco_id}")
        print(f"  estado: {detail.get('estado')}")
    finally:
        client.client.close()


def main() -> int:
    args = parser().parse_args()
    try:
        run(args)
    except (WorkflowError, OSError, KeyError, ValueError, httpx.HTTPError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
