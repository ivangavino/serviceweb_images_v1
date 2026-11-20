"""Strict Pydantic v2 contracts for mildew acquisitions and uploads."""

import hashlib
import json
import math
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CODE = re.compile(r"[A-Za-z0-9_-]{1,100}\Z", re.ASCII)
_FORM_FIELD = re.compile(r"imagen_[a-f0-9]{32}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResultadoCreate(StrictModel):
    total_imagenes: int = Field(ge=0)
    imagenes_procesadas: int = Field(ge=0)
    imagenes_con_mildiu: int = Field(ge=0)
    imagenes_sin_mildiu: int = Field(ge=0)
    imagenes_posible_mildiu: int = Field(ge=0)
    imagenes_no_procesadas: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent_counts(self) -> "ResultadoCreate":
        classified = self.imagenes_con_mildiu + self.imagenes_sin_mildiu + self.imagenes_posible_mildiu
        if self.imagenes_procesadas != classified:
            raise ValueError("Las categorías no suman imagenes_procesadas")
        if self.total_imagenes < self.imagenes_procesadas:
            raise ValueError("total_imagenes no puede ser menor que imagenes_procesadas")
        if self.imagenes_no_procesadas != self.total_imagenes - self.imagenes_procesadas:
            raise ValueError("imagenes_no_procesadas es inconsistente")
        return self


class SurcoCreate(StrictModel):
    numero_surco: int = Field(ge=1, le=9999)
    codigo_surco: str = Field(min_length=1, max_length=100)
    latitud_inicio: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitud_inicio: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitud_fin: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitud_fin: float = Field(ge=-180, le=180, allow_inf_nan=False)
    descripcion: str | None = Field(default=None, max_length=1000)
    resultados: ResultadoCreate

    @field_validator("codigo_surco")
    @classmethod
    def valid_code(cls, value: str) -> str:
        if not _CODE.fullmatch(value):
            raise ValueError("codigo_surco contiene caracteres no permitidos")
        return value

    @model_validator(mode="after")
    def different_endpoints(self) -> "SurcoCreate":
        if (self.latitud_inicio, self.longitud_inicio) == (self.latitud_fin, self.longitud_fin):
            raise ValueError("El inicio y fin del surco deben ser distintos")
        return self


class TomaCreate(StrictModel):
    client_toma_id: uuid.UUID
    terreno_id: int = Field(gt=0)
    fecha_inicio: datetime
    fecha_fin: datetime | None = None
    numero_toma_semana: int = Field(ge=1, le=20)
    modelo_ia: str = Field(min_length=1, max_length=150)
    version_modelo: str = Field(min_length=1, max_length=80)
    descripcion: str | None = Field(default=None, max_length=2000)
    surcos: list[SurcoCreate] = Field(min_length=1, max_length=20)

    @field_validator("fecha_inicio", "fecha_fin")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Las fechas deben incluir zona horaria")
        return value

    @model_validator(mode="after")
    def coherent_take(self) -> "TomaCreate":
        if self.fecha_fin is not None and self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin no puede preceder fecha_inicio")
        numbers = [item.numero_surco for item in self.surcos]
        codes = [item.codigo_surco for item in self.surcos]
        if len(numbers) != len(set(numbers)):
            raise ValueError("numero_surco repetido")
        if len(codes) != len(set(codes)):
            raise ValueError("codigo_surco repetido")
        return self

    def content_hash(self) -> str:
        normalized = self.model_dump(mode="json", exclude_none=False)
        raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class ImagenUploadMeta(StrictModel):
    client_imagen_id: uuid.UUID
    campo_archivo: str
    categoria: str
    hash_sha256: str | None = None
    fecha_captura: datetime | None = None

    @field_validator("campo_archivo")
    @classmethod
    def valid_form_field(cls, value: str) -> str:
        if not _FORM_FIELD.fullmatch(value):
            raise ValueError("campo_archivo inválido")
        return value

    @field_validator("categoria")
    @classmethod
    def valid_category(cls, value: str) -> str:
        if value not in {"con_mildiu", "sin_mildiu", "posible_mildiu"}:
            raise ValueError("Categoría inválida")
        return value

    @field_validator("hash_sha256")
    @classmethod
    def valid_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("hash_sha256 inválido")
        return value

    @field_validator("fecha_captura")
    @classmethod
    def capture_timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("fecha_captura debe incluir zona horaria")
        return value


class UploadMetadata(StrictModel):
    client_lote_id: uuid.UUID
    imagenes: list[ImagenUploadMeta] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_identifiers(self) -> "UploadMetadata":
        ids = [item.client_imagen_id for item in self.imagenes]
        fields = [item.campo_archivo for item in self.imagenes]
        if len(ids) != len(set(ids)):
            raise ValueError("client_imagen_id repetido en el lote")
        if len(fields) != len(set(fields)):
            raise ValueError("campo_archivo repetido en el lote")
        return self


def geographic_length_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine pre-check; PostgreSQL/PostGIS remains the persisted authority."""
    radius = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

