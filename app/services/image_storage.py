"""Validated staging and collision-resistant local image storage."""

import hashlib
import os
import threading
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from app.core.errors import ApiError


@dataclass(frozen=True)
class StagedImage:
    token: str
    original_name: str
    size: int
    sha256: str
    image_format: str
    extension: str


class ImageStorage:
    _pillow_lock = threading.Lock()
    CATEGORY_FOLDERS = {
        "con_mildiu": "img-hojas-con-mildiu",
        "sin_mildiu": "img-hojas-sin-mildiu",
        "posible_mildiu": "img-hojas-con-posible-mildiu",
    }

    def __init__(self, root: Path, staging_root: Path, *, max_image_bytes: int, max_pixels: int, allowed_formats: set[str]):
        self.root = root.expanduser().resolve()
        self.staging_root = staging_root.expanduser().resolve()
        self.max_image_bytes = max_image_bytes
        self.max_pixels = max_pixels
        self.allowed_formats = allowed_formats
        if self.staging_root == self.root or self.root not in self.staging_root.parents:
            raise ValueError("El staging debe estar dentro del directorio de imágenes")
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def stage(self, file_object, original_name: str | None) -> StagedImage:
        safe_name = (original_name or "imagen").replace("\\", "/").rsplit("/", 1)[-1][:255]
        if not safe_name or "\x00" in safe_name:
            raise ApiError(422, "invalid_image_name", "Nombre de imagen inválido")
        token = uuid.uuid4().hex + ".part"
        target = self._safe_staging_path(token)
        digest = hashlib.sha256()
        size = 0
        try:
            file_object.seek(0)
            with target.open("xb") as output:
                while True:
                    chunk = file_object.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_image_bytes:
                        raise ApiError(413, "image_too_large", "Una imagen supera el tamaño máximo")
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise ApiError(422, "empty_image", "No se permiten imágenes vacías")
            detected = self._verify_image(target)
            extension = ".jpg" if detected == "JPEG" else ".png"
            supplied = Path(safe_name).suffix.lower()
            allowed_extensions = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}}[detected]
            if supplied not in allowed_extensions:
                raise ApiError(422, "image_extension_mismatch", "La extensión no coincide con el formato real")
            return StagedImage(token, safe_name, size, digest.hexdigest(), detected, extension)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def _verify_image(self, path: Path) -> str:
        with self._pillow_lock:
            old_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = self.max_pixels
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(path) as image:
                        detected = image.format
                        image.verify()
                if detected not in self.allowed_formats:
                    raise ApiError(415, "unsupported_image_format", "Formato de imagen no permitido")
                return detected
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise ApiError(422, "invalid_image", "El archivo no es una imagen válida") from exc
            finally:
                Image.MAX_IMAGE_PIXELS = old_limit

    def build_relative_path(
        self,
        *,
        capture_date: datetime,
        terreno_id: int,
        numero_toma: int,
        toma_id: uuid.UUID,
        numero_surco: int,
        surco_id: uuid.UUID,
        category: str,
        image_id: uuid.UUID,
        extension: str,
    ) -> str:
        folder = self.CATEGORY_FOLDERS[category]
        day = capture_date.strftime("%d-%m-%Y")
        relative = PurePosixPath(
            f"{capture_date:%Y}",
            f"{capture_date:%m}",
            day,
            f"terreno_{terreno_id}",
            f"toma_{numero_toma:02d}_{toma_id.hex[:8]}",
            f"surco{numero_surco:02d}_{surco_id.hex[:8]}",
            folder,
            f"{image_id.hex}{extension}",
        )
        return relative.as_posix()

    def promote(self, token: str, relative_path: str) -> None:
        source = self._safe_staging_path(token)
        destination = self._safe_final_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, destination)
        source.unlink()

    def final_exists(self, relative_path: str) -> bool:
        return self._safe_final_path(relative_path).is_file()

    def staging_exists(self, token: str) -> bool:
        return self._safe_staging_path(token).is_file()

    def cleanup_staged(self, token: str) -> None:
        self._safe_staging_path(token).unlink(missing_ok=True)

    def _safe_staging_path(self, token: str) -> Path:
        if not token or Path(token).name != token:
            raise ValueError("Token temporal inseguro")
        result = (self.staging_root / token).resolve()
        if self.staging_root not in result.parents:
            raise ValueError("Ruta temporal insegura")
        return result

    def _safe_final_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Ruta relativa insegura")
        result = (self.root / candidate).resolve()
        if self.root not in result.parents or self.staging_root == result or self.staging_root in result.parents:
            raise ValueError("Ruta final insegura")
        return result

