"""Transactional business rules for takes, furrows, totals, and finalization."""

import uuid
from datetime import timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import ApiError
from app.database.connection import get_session_factory
from app.models.mildiu import (
    ImagenMildiu,
    PrototipoTerrenoMildiu,
    ResultadoMildiu,
    SurcoMildiu,
    TomaMildiu,
)
from app.models.terreno import Terreno
from app.schemas.mildiu import TomaCreate, geographic_length_meters
from app.security.auth_service import Principal


def register_take(settings: Settings, principal: Principal, payload: TomaCreate) -> dict:
    if not settings.min_surcos_por_toma <= len(payload.surcos) <= settings.max_surcos_por_toma:
        raise ApiError(422, "invalid_furrow_count", "Cantidad de surcos fuera del límite configurado")
    if payload.numero_toma_semana > settings.max_tomas_por_semana:
        raise ApiError(422, "invalid_weekly_take_number", "Número de toma semanal fuera del límite")
    content_hash = payload.content_hash()
    crop_tz = ZoneInfo(settings.crop_timezone)
    local_start = payload.fecha_inicio.astimezone(crop_tz)
    iso = local_start.isocalendar()

    try:
        with get_session_factory().begin() as session:
            existing = session.execute(
                select(TomaMildiu).where(
                    TomaMildiu.prototipo_id == principal.prototipo_id,
                    TomaMildiu.client_toma_id == payload.client_toma_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.contenido_sha256 != content_hash:
                    raise ApiError(409, "idempotency_conflict", "client_toma_id ya fue usado con contenido diferente")
                return _take_registration_response(session, existing, idempotent=True)

            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"mildiu:{payload.terreno_id}:{iso.year}:{iso.week}"},
            )
            existing = session.execute(
                select(TomaMildiu).where(
                    TomaMildiu.prototipo_id == principal.prototipo_id,
                    TomaMildiu.client_toma_id == payload.client_toma_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.contenido_sha256 != content_hash:
                    raise ApiError(409, "idempotency_conflict", "client_toma_id ya fue usado con contenido diferente")
                return _take_registration_response(session, existing, idempotent=True)

            terrain = session.execute(
                select(Terreno).where(Terreno.terreno_id == payload.terreno_id).with_for_update(read=True)
            ).scalar_one_or_none()
            if terrain is None or not terrain.activo:
                raise ApiError(404, "terrain_not_found", "Terreno inexistente o inactivo")
            authorized = session.scalar(
                select(func.count()).select_from(PrototipoTerrenoMildiu).where(
                    PrototipoTerrenoMildiu.prototipo_id == principal.prototipo_id,
                    PrototipoTerrenoMildiu.terreno_id == payload.terreno_id,
                    PrototipoTerrenoMildiu.activo.is_(True),
                )
            )
            if not authorized:
                raise ApiError(403, "terrain_forbidden", "El prototipo no está autorizado para este terreno")
            weekly_count = session.scalar(
                select(func.count()).select_from(TomaMildiu).where(
                    TomaMildiu.terreno_id == payload.terreno_id,
                    TomaMildiu.anio_iso == iso.year,
                    TomaMildiu.semana_iso == iso.week,
                )
            )
            if weekly_count >= settings.max_tomas_por_semana:
                raise ApiError(409, "weekly_limit_reached", "Se alcanzó el límite semanal del terreno")

            take = TomaMildiu(
                client_toma_id=payload.client_toma_id,
                terreno_id=payload.terreno_id,
                prototipo_id=principal.prototipo_id,
                numero_toma_semana=payload.numero_toma_semana,
                fecha_inicio=payload.fecha_inicio,
                fecha_fin=payload.fecha_fin,
                semana_iso=iso.week,
                anio_iso=iso.year,
                cantidad_surcos=len(payload.surcos),
                estado="recibida",
                descripcion=payload.descripcion,
                contenido_sha256=content_hash,
            )
            session.add(take)
            session.flush()
            for item in payload.surcos:
                approximate = geographic_length_meters(
                    item.latitud_inicio,
                    item.longitud_inicio,
                    item.latitud_fin,
                    item.longitud_fin,
                )
                margin = max(2.0, approximate * 0.01)
                if approximate < settings.min_longitud_surco_metros - margin or approximate > settings.max_longitud_surco_metros + margin:
                    raise ApiError(422, "invalid_furrow_length", "La longitud geográfica del surco está fuera del rango configurado")
                distance = session.execute(
                    text(
                        "SELECT ST_Length(ST_MakeLine("
                        "ST_SetSRID(ST_MakePoint(:lon1,:lat1),4326),"
                        "ST_SetSRID(ST_MakePoint(:lon2,:lat2),4326))::geography)"
                    ),
                    {"lon1": item.longitud_inicio, "lat1": item.latitud_inicio, "lon2": item.longitud_fin, "lat2": item.latitud_fin},
                ).scalar_one()
                if not settings.min_longitud_surco_metros <= distance <= settings.max_longitud_surco_metros:
                    raise ApiError(422, "invalid_furrow_length", "La longitud geográfica del surco está fuera del rango configurado")
                furrow = SurcoMildiu(
                    toma_id=take.toma_id,
                    numero_surco=item.numero_surco,
                    codigo_surco=item.codigo_surco,
                    latitud_inicio=item.latitud_inicio,
                    longitud_inicio=item.longitud_inicio,
                    latitud_fin=item.latitud_fin,
                    longitud_fin=item.longitud_fin,
                    longitud_metros=distance,
                    geometria=func.ST_MakeLine(
                        func.ST_SetSRID(func.ST_MakePoint(item.longitud_inicio, item.latitud_inicio), 4326),
                        func.ST_SetSRID(func.ST_MakePoint(item.longitud_fin, item.latitud_fin), 4326),
                    ),
                    descripcion=item.descripcion,
                )
                session.add(furrow)
                session.flush()
                values = item.resultados.model_dump()
                session.add(
                    ResultadoMildiu(
                        surco_id=furrow.surco_id,
                        **values,
                        modelo_ia=payload.modelo_ia,
                        version_modelo=payload.version_modelo,
                        fecha_procesamiento=payload.fecha_fin or payload.fecha_inicio,
                    )
                )
            session.flush()
            return _take_registration_response(session, take, idempotent=False)
    except ApiError:
        raise
    except IntegrityError as exc:
        raise ApiError(409, "take_conflict", "La toma entra en conflicto con un registro existente") from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "No se pudo registrar la toma") from exc


def _take_registration_response(session, take: TomaMildiu, *, idempotent: bool) -> dict:
    furrows = session.execute(
        select(SurcoMildiu).where(SurcoMildiu.toma_id == take.toma_id).order_by(SurcoMildiu.numero_surco)
    ).scalars().all()
    return {
        "toma_id": str(take.toma_id),
        "client_toma_id": str(take.client_toma_id),
        "estado": take.estado,
        "idempotent_replay": idempotent,
        "surcos": [
            {"surco_id": str(item.surco_id), "numero_surco": item.numero_surco, "codigo_surco": item.codigo_surco, "longitud_metros": round(item.longitud_metros, 2)}
            for item in furrows
        ],
    }


def finalize_take(principal: Principal, toma_id: uuid.UUID) -> dict:
    try:
        with get_session_factory().begin() as session:
            take = session.execute(
                select(TomaMildiu)
                .where(TomaMildiu.toma_id == toma_id)
                .options(selectinload(TomaMildiu.surcos).selectinload(SurcoMildiu.resultado))
                .with_for_update()
            ).scalar_one_or_none()
            _ensure_owned_take(take, principal)
            if take.estado == "completada":
                return {"toma_id": str(take.toma_id), "estado": "completada", "idempotent_replay": True}
            if len(take.surcos) != take.cantidad_surcos:
                raise ApiError(409, "incomplete_furrows", "La cantidad de surcos es incompleta")
            pending = 0
            for furrow in take.surcos:
                if furrow.resultado is None:
                    raise ApiError(409, "missing_result", "Un surco no tiene resultado")
                counts = dict(
                    session.execute(
                        select(ImagenMildiu.categoria, func.count())
                        .where(ImagenMildiu.resultado_id == furrow.resultado.resultado_id, ImagenMildiu.estado_almacenamiento == "disponible")
                        .group_by(ImagenMildiu.categoria)
                    ).all()
                )
                pending += session.scalar(
                    select(func.count()).select_from(ImagenMildiu).where(
                        ImagenMildiu.resultado_id == furrow.resultado.resultado_id,
                        ImagenMildiu.estado_almacenamiento == "pendiente",
                    )
                )
                expected = {
                    "con_mildiu": furrow.resultado.imagenes_con_mildiu,
                    "sin_mildiu": furrow.resultado.imagenes_sin_mildiu,
                    "posible_mildiu": furrow.resultado.imagenes_posible_mildiu,
                }
                if any(counts.get(category, 0) != amount for category, amount in expected.items()):
                    raise ApiError(409, "image_count_mismatch", "Las imágenes recibidas no coinciden con los resultados")
            if pending:
                raise ApiError(409, "uploads_pending", "Existen archivos pendientes")
            take.estado = "completada"
            take.ultimo_error_recuperable = None
            return {"toma_id": str(take.toma_id), "estado": take.estado, "idempotent_replay": False}
    except ApiError:
        raise
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "No se pudo finalizar la toma") from exc


def get_take_details(principal: Principal, toma_id: uuid.UUID) -> dict:
    try:
        with get_session_factory()() as session:
            take = session.execute(
                select(TomaMildiu)
                .where(TomaMildiu.toma_id == toma_id)
                .options(selectinload(TomaMildiu.surcos).selectinload(SurcoMildiu.resultado))
            ).scalar_one_or_none()
            _ensure_owned_take(take, principal)
            total = {key: 0 for key in ("total_imagenes", "imagenes_procesadas", "imagenes_con_mildiu", "imagenes_sin_mildiu", "imagenes_posible_mildiu", "imagenes_no_procesadas")}
            furrows = []
            for furrow in take.surcos:
                result = furrow.resultado
                result_data = {key: getattr(result, key) for key in total}
                for key, value in result_data.items():
                    total[key] += value
                received = dict(
                    session.execute(
                        select(ImagenMildiu.categoria, func.count())
                        .where(ImagenMildiu.resultado_id == result.resultado_id, ImagenMildiu.estado_almacenamiento == "disponible")
                        .group_by(ImagenMildiu.categoria)
                    ).all()
                )
                furrows.append(
                    {
                        "surco_id": str(furrow.surco_id),
                        "numero_surco": furrow.numero_surco,
                        "codigo_surco": furrow.codigo_surco,
                        "coordenadas": {
                            "inicio": [float(furrow.latitud_inicio), float(furrow.longitud_inicio)],
                            "fin": [float(furrow.latitud_fin), float(furrow.longitud_fin)],
                        },
                        "longitud_metros": round(furrow.longitud_metros, 2),
                        "resultado": result_data | {"modelo_ia": result.modelo_ia, "version_modelo": result.version_modelo},
                        "imagenes_recibidas": {category: received.get(category, 0) for category in ("con_mildiu", "sin_mildiu", "posible_mildiu")},
                    }
                )
            return {
                "toma_id": str(take.toma_id),
                "client_toma_id": str(take.client_toma_id),
                "terreno_id": take.terreno_id,
                "prototipo_id": take.prototipo_id,
                "estado": take.estado,
                "fecha_inicio": take.fecha_inicio.isoformat(),
                "fecha_fin": take.fecha_fin.isoformat() if take.fecha_fin else None,
                "semana_iso": take.semana_iso,
                "anio_iso": take.anio_iso,
                "surcos": furrows,
                "totales": total,
            }
    except ApiError:
        raise
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "No se pudo consultar la toma") from exc


def get_take_status(principal: Principal, toma_id: uuid.UUID) -> dict:
    details = get_take_details(principal, toma_id)
    received = sum(sum(item["imagenes_recibidas"].values()) for item in details["surcos"])
    expected = details["totales"]["imagenes_procesadas"]
    try:
        with get_session_factory()() as session:
            take = session.get(TomaMildiu, toma_id)
            pending = session.scalar(
                select(func.count()).select_from(ImagenMildiu)
                .join(ResultadoMildiu, ResultadoMildiu.resultado_id == ImagenMildiu.resultado_id)
                .join(SurcoMildiu, SurcoMildiu.surco_id == ResultadoMildiu.surco_id)
                .where(SurcoMildiu.toma_id == toma_id, ImagenMildiu.estado_almacenamiento == "pendiente")
            )
            return {
                "toma_id": str(toma_id),
                "estado": details["estado"],
                "surcos_recibidos": len(details["surcos"]),
                "imagenes_recibidas": received,
                "imagenes_esperadas": expected,
                "imagenes_pendientes": max(expected - received, 0),
                "archivos_en_recuperacion": pending,
                "ultimo_error_recuperable": take.ultimo_error_recuperable,
            }
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "No se pudo consultar el estado") from exc


def _ensure_owned_take(take: TomaMildiu | None, principal: Principal) -> None:
    if take is None:
        raise ApiError(404, "take_not_found", "Toma no encontrada")
    if take.prototipo_id != principal.prototipo_id:
        raise ApiError(403, "take_forbidden", "La toma no pertenece al prototipo")

