"""Idempotent image batches with a recoverable database/filesystem handoff."""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import Settings
from app.core.errors import ApiError, RecoverableStorageError
from app.database.connection import get_session_factory
from app.models.mildiu import (
    ImagenMildiu,
    LoteImagenesMildiu,
    ResultadoMildiu,
    SurcoMildiu,
    TomaMildiu,
)
from app.schemas.mildiu import ImagenUploadMeta, UploadMetadata
from app.security.auth_service import Principal
from app.services.image_storage import ImageStorage, StagedImage

log = logging.getLogger("quinuaalert.upload")


@dataclass(frozen=True)
class PreparedImage:
    metadata: ImagenUploadMeta
    staged: StagedImage


def make_storage(settings: Settings) -> ImageStorage:
    return ImageStorage(
        settings.image_storage_root,
        settings.image_staging_root,
        max_image_bytes=settings.max_image_bytes,
        max_pixels=settings.max_image_pixels,
        allowed_formats=settings.allowed_formats,
    )


def stage_batch(settings: Settings, metadata: UploadMetadata, uploads: dict[str, object]) -> tuple[ImageStorage, list[PreparedImage]]:
    if len(metadata.imagenes) > settings.max_files_per_batch:
        raise ApiError(413, "too_many_files", "El lote supera la cantidad máxima de imágenes")
    expected_fields = {item.campo_archivo for item in metadata.imagenes}
    if set(uploads) != expected_fields:
        raise ApiError(422, "file_mapping_mismatch", "Los archivos no coinciden con metadata")
    storage = make_storage(settings)
    prepared: list[PreparedImage] = []
    total = 0
    try:
        for item in metadata.imagenes:
            upload = uploads[item.campo_archivo]
            staged = storage.stage(upload.file, upload.filename)
            prepared.append(PreparedImage(item, staged))
            if item.hash_sha256 is not None and item.hash_sha256 != staged.sha256:
                raise ApiError(422, "image_hash_mismatch", "El SHA-256 de una imagen no coincide")
            total += staged.size
            if total > settings.max_upload_total_bytes:
                raise ApiError(413, "upload_too_large", "El lote supera el tamaño máximo")
        return storage, prepared
    except Exception:
        for item in prepared:
            storage.cleanup_staged(item.staged.token)
        raise


def persist_batch(
    settings: Settings,
    principal: Principal,
    toma_id: uuid.UUID,
    surco_id: uuid.UUID,
    metadata: UploadMetadata,
    storage: ImageStorage,
    prepared: list[PreparedImage],
) -> dict:
    batch_hash = _batch_hash(metadata, prepared)
    records_persisted = False
    new_image_ids: list[uuid.UUID] = []
    duplicate_ids: list[str] = []
    existing_batch_id: uuid.UUID | None = None
    try:
        with get_session_factory().begin() as session:
            row = session.execute(
                select(TomaMildiu, SurcoMildiu, ResultadoMildiu)
                .join(SurcoMildiu, SurcoMildiu.toma_id == TomaMildiu.toma_id)
                .join(ResultadoMildiu, ResultadoMildiu.surco_id == SurcoMildiu.surco_id)
                .where(TomaMildiu.toma_id == toma_id, SurcoMildiu.surco_id == surco_id)
                .with_for_update(of=TomaMildiu)
            ).first()
            if row is None:
                raise ApiError(404, "furrow_not_found", "Toma o surco no encontrado")
            take, furrow, result = row
            if take.prototipo_id != principal.prototipo_id:
                raise ApiError(403, "take_forbidden", "La toma no pertenece al prototipo")
            if take.estado == "completada":
                raise ApiError(409, "take_completed", "No se puede modificar una toma completada")

            existing_batch = session.execute(
                select(LoteImagenesMildiu).where(
                    LoteImagenesMildiu.prototipo_id == principal.prototipo_id,
                    LoteImagenesMildiu.client_lote_id == metadata.client_lote_id,
                )
            ).scalar_one_or_none()
            if existing_batch is not None:
                same_operation = (
                    existing_batch.toma_id == toma_id
                    and existing_batch.surco_id == surco_id
                    and existing_batch.contenido_sha256 == batch_hash
                )
                if not same_operation:
                    raise ApiError(409, "idempotency_conflict", "client_lote_id ya fue usado con contenido diferente")
                existing_batch_id = existing_batch.lote_id
                for item in prepared:
                    storage.cleanup_staged(item.staged.token)
            else:
                client_ids = [item.metadata.client_imagen_id for item in prepared]
                hashes = [item.staged.sha256 for item in prepared]
                existing_images = session.execute(
                    select(ImagenMildiu).where(
                        ImagenMildiu.resultado_id == result.resultado_id,
                        or_(ImagenMildiu.client_imagen_id.in_(client_ids), ImagenMildiu.hash_sha256.in_(hashes)),
                    )
                ).scalars().all()
                by_client = {image.client_imagen_id: image for image in existing_images}
                by_hash = {image.hash_sha256: image for image in existing_images}
                new_items: list[PreparedImage] = []
                for item in prepared:
                    previous_id = by_client.get(item.metadata.client_imagen_id)
                    previous_hash = by_hash.get(item.staged.sha256)
                    previous = previous_id or previous_hash
                    if previous is None:
                        new_items.append(item)
                        continue
                    exact = (
                        previous.client_imagen_id == item.metadata.client_imagen_id
                        and previous.hash_sha256 == item.staged.sha256
                        and previous.categoria == item.metadata.categoria
                    )
                    if not exact:
                        raise ApiError(409, "image_id_conflict", "Una imagen reutiliza identificador o contenido de forma incompatible")
                    if previous.estado_almacenamiento != "disponible":
                        raise ApiError(409, "image_pending", "La imagen ya existe pero su lote original requiere recuperación")
                    duplicate_ids.append(str(item.metadata.client_imagen_id))
                    storage.cleanup_staged(item.staged.token)

                batch = LoteImagenesMildiu(
                    client_lote_id=metadata.client_lote_id,
                    prototipo_id=principal.prototipo_id,
                    toma_id=toma_id,
                    surco_id=surco_id,
                    contenido_sha256=batch_hash,
                    cantidad_imagenes=len(metadata.imagenes),
                    estado="completado" if not new_items else "procesando",
                )
                session.add(batch)
                session.flush()
                local_date = take.fecha_inicio.astimezone(ZoneInfo(settings.crop_timezone))
                for item in new_items:
                    image_id = uuid.uuid4()
                    relative = storage.build_relative_path(
                        capture_date=local_date,
                        terreno_id=take.terreno_id,
                        numero_toma=take.numero_toma_semana,
                        toma_id=take.toma_id,
                        numero_surco=furrow.numero_surco,
                        surco_id=furrow.surco_id,
                        category=item.metadata.categoria,
                        image_id=image_id,
                        extension=item.staged.extension,
                    )
                    session.add(
                        ImagenMildiu(
                            imagen_id=image_id,
                            client_imagen_id=item.metadata.client_imagen_id,
                            resultado_id=result.resultado_id,
                            lote_id=batch.lote_id,
                            nombre_original=item.staged.original_name,
                            nombre_almacenado=f"{image_id.hex}{item.staged.extension}",
                            ruta_relativa=relative,
                            token_temporal=item.staged.token,
                            categoria=item.metadata.categoria,
                            formato=item.staged.image_format,
                            tamano_bytes=item.staged.size,
                            hash_sha256=item.staged.sha256,
                            fecha_captura=item.metadata.fecha_captura,
                            estado_almacenamiento="pendiente",
                        )
                    )
                    new_image_ids.append(image_id)
                take.estado = "procesando"
                take.ultimo_error_recuperable = None
                existing_batch_id = batch.lote_id
        records_persisted = True

        if not new_image_ids:
            if existing_batch_id is not None:
                recover_batch(storage, existing_batch_id)
            return _batch_response(
                existing_batch_id,
                metadata.client_lote_id,
                duplicate_ids,
                idempotent=existing_batch is not None,
                estado=_batch_state(existing_batch_id),
            )

        try:
            _complete_images(storage, existing_batch_id, new_image_ids)
        except Exception as exc:
            log.error("Filesystem promotion pending for batch_id=%s", existing_batch_id)
            _record_recoverable_error(existing_batch_id, toma_id, str(exc))
            raise RecoverableStorageError() from exc
        return _batch_response(existing_batch_id, metadata.client_lote_id, duplicate_ids, idempotent=False)
    except ApiError:
        raise
    except IntegrityError as exc:
        raise ApiError(409, "upload_conflict", "La carga entra en conflicto con datos existentes") from exc
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "No se pudo registrar la carga") from exc
    finally:
        if not records_persisted:
            for item in prepared:
                storage.cleanup_staged(item.staged.token)


def recover_batch(storage: ImageStorage, batch_id: uuid.UUID) -> None:
    """Finish pending moves. Safe to call repeatedly after crashes or retries."""
    with get_session_factory().begin() as session:
        batch = session.execute(
            select(LoteImagenesMildiu).where(LoteImagenesMildiu.lote_id == batch_id).with_for_update()
        ).scalar_one_or_none()
        if batch is None or batch.estado == "completado":
            return
        images = session.execute(
            select(ImagenMildiu).where(ImagenMildiu.lote_id == batch_id).with_for_update()
        ).scalars().all()
        failed = False
        for image in images:
            if image.estado_almacenamiento == "disponible":
                continue
            if storage.final_exists(image.ruta_relativa):
                if image.token_temporal:
                    storage.cleanup_staged(image.token_temporal)
                image.estado_almacenamiento = "disponible"
                image.token_temporal = None
                continue
            if image.token_temporal and storage.staging_exists(image.token_temporal):
                storage.promote(image.token_temporal, image.ruta_relativa)
                image.estado_almacenamiento = "disponible"
                image.token_temporal = None
            else:
                image.estado_almacenamiento = "fallido"
                failed = True
        batch.estado = "fallido" if failed else "completado"
        batch.ultimo_error = "Falta el archivo temporal y final" if failed else None
        take = session.get(TomaMildiu, batch.toma_id)
        if take is not None:
            take.ultimo_error_recuperable = batch.ultimo_error


def recover_pending_uploads(settings: Settings, limit: int = 100) -> int:
    storage = make_storage(settings)
    try:
        with get_session_factory()() as session:
            batch_ids = session.scalars(
                select(LoteImagenesMildiu.lote_id)
                .where(LoteImagenesMildiu.estado == "procesando")
                .order_by(LoteImagenesMildiu.creado_en)
                .limit(limit)
            ).all()
        for batch_id in batch_ids:
            try:
                recover_batch(storage, batch_id)
            except Exception:
                log.exception("Pending batch recovery failed batch_id=%s", batch_id)
        return len(batch_ids)
    except SQLAlchemyError:
        log.exception("Could not inspect pending image batches")
        return 0


def _complete_images(storage: ImageStorage, batch_id: uuid.UUID, image_ids: list[uuid.UUID]) -> None:
    with get_session_factory().begin() as session:
        batch = session.execute(
            select(LoteImagenesMildiu).where(LoteImagenesMildiu.lote_id == batch_id).with_for_update()
        ).scalar_one()
        images = session.execute(
            select(ImagenMildiu).where(ImagenMildiu.imagen_id.in_(image_ids)).with_for_update()
        ).scalars().all()
        for image in images:
            if storage.final_exists(image.ruta_relativa):
                if image.token_temporal:
                    storage.cleanup_staged(image.token_temporal)
                image.estado_almacenamiento = "disponible"
                image.token_temporal = None
                continue
            if not image.token_temporal:
                raise FileNotFoundError("No existe token temporal")
            storage.promote(image.token_temporal, image.ruta_relativa)
            image.estado_almacenamiento = "disponible"
            image.token_temporal = None
        batch.estado = "completado"
        batch.ultimo_error = None


def _record_recoverable_error(batch_id: uuid.UUID, toma_id: uuid.UUID, error: str) -> None:
    try:
        with get_session_factory().begin() as session:
            batch = session.get(LoteImagenesMildiu, batch_id)
            take = session.get(TomaMildiu, toma_id)
            safe_error = error[:500]
            if batch is not None:
                batch.ultimo_error = safe_error
            if take is not None:
                take.ultimo_error_recuperable = safe_error
    except SQLAlchemyError:
        log.error("Could not persist recoverable upload error batch_id=%s", batch_id)


def _batch_hash(metadata: UploadMetadata, prepared: list[PreparedImage]) -> str:
    data = metadata.model_dump(mode="json")
    actual = {
        str(item.metadata.client_imagen_id): {
            "sha256": item.staged.sha256,
            "size": item.staged.size,
            "format": item.staged.image_format,
        }
        for item in prepared
    }
    raw = json.dumps({"metadata": data, "actual": actual}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _batch_state(batch_id: uuid.UUID) -> str:
    with get_session_factory()() as session:
        return session.scalar(
            select(LoteImagenesMildiu.estado).where(LoteImagenesMildiu.lote_id == batch_id)
        )


def _batch_response(
    batch_id,
    client_lote_id,
    duplicates: list[str],
    *,
    idempotent: bool,
    estado: str = "completado",
) -> dict:
    return {
        "lote_id": str(batch_id),
        "client_lote_id": str(client_lote_id),
        "estado": estado,
        "idempotent_replay": idempotent,
        "imagenes_duplicadas": duplicates,
    }

