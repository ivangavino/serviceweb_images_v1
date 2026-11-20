"""Reference Raspberry Pi client that signs the exact bytes sent by HTTPX."""

import argparse
import hashlib
import json
import mimetypes
import os
import time
import uuid
from pathlib import Path

import httpx

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security.hmac_auth import canonical_request, sign


class SignedClient:
    def __init__(self, base_url: str, device_id: str, key_id: str, secret: str):
        self.device_id = device_id
        self.key_id = key_id
        self.secret = secret
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=60, verify=True)

    def send(self, method: str, path: str, **kwargs) -> httpx.Response:
        request = self.client.build_request(method, path, **kwargs)
        # read() materializes the final multipart boundary and bytes. The same
        # Request instance is then sent, so no library rebuild can change them.
        body = request.read()
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        query = request.url.query.decode("ascii") if isinstance(request.url.query, bytes) else request.url.query
        message = canonical_request(method.upper(), request.url.path, query, self.device_id, self.key_id, timestamp, nonce, digest)
        request.headers.update(
            {
                "X-Device-ID": self.device_id,
                "X-Key-ID": self.key_id,
                "X-Timestamp": timestamp,
                "X-Nonce": nonce,
                "X-Content-SHA256": digest,
                "X-Signature": sign(self.secret, message),
            }
        )
        return self.client.send(request)


def print_response(response: httpx.Response) -> None:
    print(f"HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(response.text)
    response.raise_for_status()


def register(client: SignedClient, args) -> None:
    body = Path(args.payload).read_bytes()
    print_response(client.send("POST", "/api/v1/mildiu/tomas", content=body, headers={"Content-Type": "application/json"}))


def upload(client: SignedClient, args) -> None:
    lote_id = uuid.UUID(args.lote_id) if args.lote_id else uuid.uuid4()
    files = []
    images = []
    handles = []
    try:
        for specification in args.image:
            category, raw_path = specification.split("=", 1)
            path = Path(raw_path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            image_id = uuid.uuid5(lote_id, f"{path.resolve()}:{digest}")
            field = f"imagen_{image_id.hex}"
            images.append(
                {
                    "client_imagen_id": str(image_id),
                    "campo_archivo": field,
                    "categoria": category,
                    "hash_sha256": digest,
                }
            )
            handle = path.open("rb")
            handles.append(handle)
            files.append((field, (path.name, handle, mimetypes.guess_type(path.name)[0] or "application/octet-stream")))
        metadata = json.dumps({"client_lote_id": str(lote_id), "imagenes": images}, separators=(",", ":"), ensure_ascii=False)
        response = client.send(
            "POST",
            f"/api/v1/mildiu/tomas/{args.toma_id}/surcos/{args.surco_id}/imagenes",
            data={"metadata": metadata},
            files=files,
        )
        print(f"client_lote_id para reintentos: {lote_id}")
        print_response(response)
    finally:
        for handle in handles:
            handle.close()


def empty_post(client: SignedClient, args) -> None:
    print_response(client.send("POST", f"/api/v1/mildiu/tomas/{args.toma_id}/finalizar", content=b""))


def get(client: SignedClient, args) -> None:
    suffix = "/estado" if args.command == "status" else ""
    print_response(client.send("GET", f"/api/v1/mildiu/tomas/{args.toma_id}{suffix}", content=b""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cliente firmado QuinuAlert Mildiu")
    parser.add_argument("--base-url", default=os.getenv("QUINUALERT_BASE_URL", "https://localhost:8443"))
    parser.add_argument("--device-id", default=os.getenv("QUINUALERT_DEVICE_ID"), required=os.getenv("QUINUALERT_DEVICE_ID") is None)
    parser.add_argument("--key-id", default=os.getenv("QUINUALERT_KEY_ID"), required=os.getenv("QUINUALERT_KEY_ID") is None)
    commands = parser.add_subparsers(dest="command", required=True)
    register_parser = commands.add_parser("register")
    register_parser.add_argument("payload")
    register_parser.set_defaults(handler=register)
    upload_parser = commands.add_parser("upload")
    upload_parser.add_argument("toma_id")
    upload_parser.add_argument("surco_id")
    upload_parser.add_argument("--lote-id", help="Reutilice este UUID solo para reintentar el mismo contenido")
    upload_parser.add_argument("--image", action="append", required=True, metavar="CATEGORIA=RUTA")
    upload_parser.set_defaults(handler=upload)
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument("toma_id")
    finish_parser.set_defaults(handler=empty_post)
    read_parser = commands.add_parser("get")
    read_parser.add_argument("toma_id")
    read_parser.set_defaults(handler=get)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("toma_id")
    status_parser.set_defaults(handler=get)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    secret = os.getenv("QUINUALERT_DEVICE_SECRET")
    if not secret:
        raise SystemExit("Defina QUINUALERT_DEVICE_SECRET")
    args.handler(SignedClient(args.base_url, args.device_id, args.key_id, secret), args)

