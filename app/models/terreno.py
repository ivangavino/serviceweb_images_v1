"""Read-only mapping of the verified existing terrenos table."""

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Terreno(Base):
    __tablename__ = "terrenos"

    terreno_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    propietario_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    direccion: Mapped[str | None] = mapped_column(Text)
    area: Mapped[float] = mapped_column(Float, nullable=False)
    poligono: Mapped[object] = mapped_column(Geometry("POLYGON", srid=4326), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    actualizado_en: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

