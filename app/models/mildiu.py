"""SQLAlchemy 2.x models for the mildew acquisition pipeline."""

import uuid
from datetime import datetime
from decimal import Decimal

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PrototipoMildiu(Base):
    __tablename__ = "prototipos_mildiu"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('activo','inactivo','mantenimiento','revocado')",
            name="ck_prototipos_mildiu_estado",
        ),
    )

    prototipo_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    codigo: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="activo")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    ultima_comunicacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    credenciales: Mapped[list["CredencialPrototipoMildiu"]] = relationship(back_populates="prototipo", cascade="all, delete-orphan")
    tomas: Mapped[list["TomaMildiu"]] = relationship(back_populates="prototipo")


class CredencialPrototipoMildiu(Base):
    __tablename__ = "credenciales_prototipo_mildiu"
    __table_args__ = (
        UniqueConstraint("prototipo_id", "key_id", name="uq_credencial_prototipo_key"),
        CheckConstraint("estado IN ('activa','retirada','revocada')", name="ck_credencial_mildiu_estado"),
    )

    credencial_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    prototipo_id: Mapped[int] = mapped_column(ForeignKey("prototipos_mildiu.prototipo_id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str] = mapped_column(String(100), nullable=False)
    secreto_cifrado: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="activa")
    valida_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valida_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    rotada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prototipo: Mapped[PrototipoMildiu] = relationship(back_populates="credenciales")


class PrototipoTerrenoMildiu(Base):
    __tablename__ = "prototipos_terrenos_mildiu"

    prototipo_id: Mapped[int] = mapped_column(ForeignKey("prototipos_mildiu.prototipo_id", ondelete="CASCADE"), primary_key=True)
    terreno_id: Mapped[int] = mapped_column(ForeignKey("terrenos.terreno_id", ondelete="CASCADE"), primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class NonceMildiu(Base):
    __tablename__ = "nonces_mildiu"
    __table_args__ = (
        UniqueConstraint("prototipo_id", "credencial_id", "nonce", name="uq_nonce_mildiu_credencial"),
        Index("ix_nonces_mildiu_expira", "expira_en"),
    )

    nonce_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    prototipo_id: Mapped[int] = mapped_column(ForeignKey("prototipos_mildiu.prototipo_id", ondelete="CASCADE"), nullable=False)
    credencial_id: Mapped[int] = mapped_column(ForeignKey("credenciales_prototipo_mildiu.credencial_id", ondelete="CASCADE"), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp_solicitud: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TomaMildiu(Base):
    __tablename__ = "tomas_mildiu"
    __table_args__ = (
        UniqueConstraint("prototipo_id", "client_toma_id", name="uq_toma_mildiu_cliente"),
        UniqueConstraint("terreno_id", "anio_iso", "semana_iso", "numero_toma_semana", name="uq_toma_mildiu_semana_numero"),
        CheckConstraint("numero_toma_semana > 0", name="ck_toma_mildiu_numero"),
        CheckConstraint("cantidad_surcos > 0", name="ck_toma_mildiu_surcos"),
        CheckConstraint("estado IN ('recibida','procesando','completada','fallida')", name="ck_toma_mildiu_estado"),
        Index("ix_tomas_mildiu_terreno_fecha", "terreno_id", "fecha_inicio"),
        Index("ix_tomas_mildiu_prototipo_fecha", "prototipo_id", "fecha_inicio"),
    )

    toma_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_toma_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    terreno_id: Mapped[int] = mapped_column(ForeignKey("terrenos.terreno_id", ondelete="RESTRICT"), nullable=False)
    prototipo_id: Mapped[int] = mapped_column(ForeignKey("prototipos_mildiu.prototipo_id", ondelete="RESTRICT"), nullable=False)
    numero_toma_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    semana_iso: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    anio_iso: Mapped[int] = mapped_column(Integer, nullable=False)
    cantidad_surcos: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="recibida")
    descripcion: Mapped[str | None] = mapped_column(Text)
    contenido_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ultimo_error_recuperable: Mapped[str | None] = mapped_column(String(500))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    prototipo: Mapped[PrototipoMildiu] = relationship(back_populates="tomas")
    surcos: Mapped[list["SurcoMildiu"]] = relationship(back_populates="toma", cascade="all, delete-orphan", order_by="SurcoMildiu.numero_surco")


class SurcoMildiu(Base):
    __tablename__ = "surcos_mildiu"
    __table_args__ = (
        UniqueConstraint("toma_id", "numero_surco", name="uq_surco_mildiu_numero"),
        UniqueConstraint("toma_id", "codigo_surco", name="uq_surco_mildiu_codigo"),
        CheckConstraint("latitud_inicio BETWEEN -90 AND 90", name="ck_surco_lat_inicio"),
        CheckConstraint("latitud_fin BETWEEN -90 AND 90", name="ck_surco_lat_fin"),
        CheckConstraint("longitud_inicio BETWEEN -180 AND 180", name="ck_surco_lon_inicio"),
        CheckConstraint("longitud_fin BETWEEN -180 AND 180", name="ck_surco_lon_fin"),
        CheckConstraint("longitud_metros > 0", name="ck_surco_longitud_metros"),
        Index("ix_surcos_mildiu_geometria", "geometria", postgresql_using="gist"),
    )

    surco_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    toma_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tomas_mildiu.toma_id", ondelete="CASCADE"), nullable=False)
    numero_surco: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    codigo_surco: Mapped[str] = mapped_column(String(100), nullable=False)
    latitud_inicio: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud_inicio: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    latitud_fin: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud_fin: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitud_metros: Mapped[float] = mapped_column(Float, nullable=False)
    geometria: Mapped[object] = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    toma: Mapped[TomaMildiu] = relationship(back_populates="surcos")
    resultado: Mapped["ResultadoMildiu"] = relationship(back_populates="surco", cascade="all, delete-orphan", uselist=False)


class ResultadoMildiu(Base):
    __tablename__ = "resultados_mildiu"
    __table_args__ = (
        CheckConstraint("total_imagenes >= 0 AND imagenes_procesadas >= 0 AND imagenes_con_mildiu >= 0 AND imagenes_sin_mildiu >= 0 AND imagenes_posible_mildiu >= 0 AND imagenes_no_procesadas >= 0", name="ck_resultados_mildiu_no_negativos"),
        CheckConstraint("total_imagenes >= imagenes_procesadas", name="ck_resultados_mildiu_total"),
        CheckConstraint("imagenes_procesadas = imagenes_con_mildiu + imagenes_sin_mildiu + imagenes_posible_mildiu", name="ck_resultados_mildiu_clasificacion"),
        CheckConstraint("imagenes_no_procesadas = total_imagenes - imagenes_procesadas", name="ck_resultados_mildiu_no_procesadas"),
    )

    resultado_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    surco_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surcos_mildiu.surco_id", ondelete="CASCADE"), unique=True, nullable=False)
    total_imagenes: Mapped[int] = mapped_column(Integer, nullable=False)
    imagenes_procesadas: Mapped[int] = mapped_column(Integer, nullable=False)
    imagenes_con_mildiu: Mapped[int] = mapped_column(Integer, nullable=False)
    imagenes_sin_mildiu: Mapped[int] = mapped_column(Integer, nullable=False)
    imagenes_posible_mildiu: Mapped[int] = mapped_column(Integer, nullable=False)
    imagenes_no_procesadas: Mapped[int] = mapped_column(Integer, nullable=False)
    modelo_ia: Mapped[str] = mapped_column(String(150), nullable=False)
    version_modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    fecha_procesamiento: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    surco: Mapped[SurcoMildiu] = relationship(back_populates="resultado")
    imagenes: Mapped[list["ImagenMildiu"]] = relationship(back_populates="resultado")


class LoteImagenesMildiu(Base):
    __tablename__ = "lotes_imagenes_mildiu"
    __table_args__ = (
        UniqueConstraint("prototipo_id", "client_lote_id", name="uq_lote_mildiu_cliente"),
        CheckConstraint("estado IN ('procesando','completado','fallido')", name="ck_lote_mildiu_estado"),
        Index("ix_lotes_mildiu_toma_surco", "toma_id", "surco_id"),
    )

    lote_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_lote_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    prototipo_id: Mapped[int] = mapped_column(ForeignKey("prototipos_mildiu.prototipo_id", ondelete="RESTRICT"), nullable=False)
    toma_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tomas_mildiu.toma_id", ondelete="RESTRICT"), nullable=False)
    surco_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("surcos_mildiu.surco_id", ondelete="RESTRICT"), nullable=False)
    contenido_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cantidad_imagenes: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default="procesando")
    ultimo_error: Mapped[str | None] = mapped_column(String(500))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ImagenMildiu(Base):
    __tablename__ = "imagenes_mildiu"
    __table_args__ = (
        ForeignKeyConstraint(["lote_id"], ["lotes_imagenes_mildiu.lote_id"], ondelete="RESTRICT"),
        UniqueConstraint("resultado_id", "client_imagen_id", name="uq_imagen_mildiu_cliente"),
        UniqueConstraint("resultado_id", "hash_sha256", name="uq_imagen_mildiu_hash"),
        CheckConstraint("categoria IN ('con_mildiu','sin_mildiu','posible_mildiu')", name="ck_imagen_mildiu_categoria"),
        CheckConstraint("formato IN ('JPEG','PNG')", name="ck_imagen_mildiu_formato"),
        CheckConstraint("tamano_bytes > 0", name="ck_imagen_mildiu_tamano"),
        CheckConstraint("estado_almacenamiento IN ('pendiente','disponible','fallido')", name="ck_imagen_mildiu_almacenamiento"),
        Index("ix_imagenes_mildiu_categoria", "categoria"),
        Index("ix_imagenes_mildiu_lote", "lote_id"),
    )

    imagen_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_imagen_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    resultado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resultados_mildiu.resultado_id", ondelete="RESTRICT"), nullable=False)
    lote_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    nombre_original: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_almacenado: Mapped[str] = mapped_column(String(100), nullable=False)
    ruta_relativa: Mapped[str] = mapped_column(String(1000), nullable=False)
    token_temporal: Mapped[str | None] = mapped_column(String(100))
    categoria: Mapped[str] = mapped_column(String(30), nullable=False)
    formato: Mapped[str] = mapped_column(String(10), nullable=False)
    tamano_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fecha_captura: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado_almacenamiento: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pendiente")
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    resultado: Mapped[ResultadoMildiu] = relationship(back_populates="imagenes")

