"""Provision, authorize, rotate, or revoke a mildew prototype.

The plaintext device secret is read only from DEVICE_HMAC_SECRET and is never
printed or stored unencrypted.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import get_settings
from app.database.connection import get_session_factory
from app.models.mildiu import CredencialPrototipoMildiu, PrototipoMildiu, PrototipoTerrenoMildiu
from app.models.terreno import Terreno
from app.security.secrets import SecretCipher


def device_secret() -> str:
    value = os.getenv("DEVICE_HMAC_SECRET")
    if not value:
        raise SystemExit("Defina DEVICE_HMAC_SECRET (mínimo 32 bytes) solo para esta ejecución")
    return value


def create(args) -> None:
    settings = get_settings()
    encrypted = SecretCipher(settings.hmac_master_key.get_secret_value()).encrypt(device_secret())
    with get_session_factory().begin() as session:
        if session.scalar(select(PrototipoMildiu.prototipo_id).where(PrototipoMildiu.codigo == args.codigo)):
            raise SystemExit("El código de prototipo ya existe")
        prototype = PrototipoMildiu(codigo=args.codigo, nombre=args.nombre, descripcion=args.descripcion, estado="activo")
        session.add(prototype)
        session.flush()
        session.add(CredencialPrototipoMildiu(prototipo_id=prototype.prototipo_id, key_id=args.key_id, secreto_cifrado=encrypted))
        if args.terreno_id is not None:
            terrain = session.get(Terreno, args.terreno_id)
            if terrain is None or not terrain.activo:
                raise SystemExit("El terreno no existe o está inactivo")
            session.add(PrototipoTerrenoMildiu(prototipo_id=prototype.prototipo_id, terreno_id=args.terreno_id))
    print(f"Prototipo creado: {args.codigo}; key_id={args.key_id}")


def rotate(args) -> None:
    settings = get_settings()
    encrypted = SecretCipher(settings.hmac_master_key.get_secret_value()).encrypt(device_secret())
    now = datetime.now(timezone.utc)
    with get_session_factory().begin() as session:
        prototype = session.execute(
            select(PrototipoMildiu).where(PrototipoMildiu.codigo == args.codigo).with_for_update()
        ).scalar_one_or_none()
        if prototype is None:
            raise SystemExit("Prototipo no encontrado")
        existing = session.scalar(
            select(CredencialPrototipoMildiu.credencial_id).where(
                CredencialPrototipoMildiu.prototipo_id == prototype.prototipo_id,
                CredencialPrototipoMildiu.key_id == args.key_id,
            )
        )
        if existing:
            raise SystemExit("key_id ya existe para el prototipo")
        for credential in session.scalars(
            select(CredencialPrototipoMildiu).where(
                CredencialPrototipoMildiu.prototipo_id == prototype.prototipo_id,
                CredencialPrototipoMildiu.estado == "activa",
            )
        ):
            credential.estado = "retirada"
            credential.valida_hasta = now
            credential.rotada_en = now
        session.add(CredencialPrototipoMildiu(prototipo_id=prototype.prototipo_id, key_id=args.key_id, secreto_cifrado=encrypted))
    print(f"Credencial rotada: {args.codigo}; key_id={args.key_id}")


def authorize(args) -> None:
    with get_session_factory().begin() as session:
        prototype = session.execute(select(PrototipoMildiu).where(PrototipoMildiu.codigo == args.codigo)).scalar_one_or_none()
        terrain = session.get(Terreno, args.terreno_id)
        if prototype is None or terrain is None or not terrain.activo:
            raise SystemExit("Prototipo o terreno activo no encontrado")
        authorization = session.get(PrototipoTerrenoMildiu, (prototype.prototipo_id, args.terreno_id))
        if authorization is None:
            session.add(PrototipoTerrenoMildiu(prototipo_id=prototype.prototipo_id, terreno_id=args.terreno_id))
        else:
            authorization.activo = True
    print(f"Terreno autorizado: {args.codigo}; terreno_id={args.terreno_id}")


def revoke(args) -> None:
    with get_session_factory().begin() as session:
        prototype = session.execute(
            select(PrototipoMildiu).where(PrototipoMildiu.codigo == args.codigo).with_for_update()
        ).scalar_one_or_none()
        if prototype is None:
            raise SystemExit("Prototipo no encontrado")
        prototype.estado = "revocado"
        for credential in session.scalars(
            select(CredencialPrototipoMildiu).where(CredencialPrototipoMildiu.prototipo_id == prototype.prototipo_id)
        ):
            credential.estado = "revocada"
            credential.valida_hasta = datetime.now(timezone.utc)
    print(f"Prototipo revocado: {args.codigo}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Administración segura de prototipos de mildiu")
    commands = result.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--codigo", required=True)
    create_parser.add_argument("--nombre", required=True)
    create_parser.add_argument("--descripcion")
    create_parser.add_argument("--key-id", required=True)
    create_parser.add_argument("--terreno-id", type=int)
    create_parser.set_defaults(handler=create)
    rotate_parser = commands.add_parser("rotate")
    rotate_parser.add_argument("--codigo", required=True)
    rotate_parser.add_argument("--key-id", required=True)
    rotate_parser.set_defaults(handler=rotate)
    authorize_parser = commands.add_parser("authorize")
    authorize_parser.add_argument("--codigo", required=True)
    authorize_parser.add_argument("--terreno-id", required=True, type=int)
    authorize_parser.set_defaults(handler=authorize)
    revoke_parser = commands.add_parser("revoke")
    revoke_parser.add_argument("--codigo", required=True)
    revoke_parser.set_defaults(handler=revoke)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    try:
        arguments.handler(arguments)
    finally:
        os.environ.pop("DEVICE_HMAC_SECRET", None)

