"""Environment-backed and strictly validated service configuration."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Runtime settings. Secrets are hidden from validation errors and reprs."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    db_host: str = Field(min_length=1)
    db_port: int = Field(ge=1, le=65535)
    db_name: str = Field(min_length=1)
    db_user: str = Field(min_length=1)
    db_password: SecretStr
    db_echo: bool = False
    log_level: str = "INFO"

    hmac_master_key: SecretStr
    hmac_timestamp_tolerance: int = Field(default=300, ge=30, le=3600)
    nonce_ttl: int = Field(default=600, ge=60, le=86400)
    nonce_cleanup_interval: int = Field(default=300, ge=30, le=86400)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)
    enforce_https: bool = False
    request_body_timeout_seconds: int = Field(default=60, ge=5, le=3600)

    crop_timezone: str = "America/Lima"
    max_tomas_por_semana: int = Field(default=2, ge=1, le=20)
    min_surcos_por_toma: int = Field(default=1, ge=1, le=20)
    max_surcos_por_toma: int = Field(default=20, ge=1, le=20)
    min_longitud_surco_metros: float = Field(default=30, gt=0, le=10000)
    max_longitud_surco_metros: float = Field(default=100, gt=0, le=10000)

    image_storage_root: Path = Path("./imagenes_mildiu")
    image_staging_root: Path = Path("./imagenes_mildiu/.staging")
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_upload_total_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_metadata_request_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_files_per_batch: int = Field(default=25, ge=1, le=500)
    max_image_pixels: int = Field(default=50_000_000, ge=1_000_000)
    allowed_image_formats: str = "JPEG,PNG"

    @field_validator("db_password")
    @classmethod
    def nonempty_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("DB_PASSWORD no puede estar vacía")
        return value

    @field_validator("hmac_master_key")
    @classmethod
    def valid_fernet_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("HMAC_MASTER_KEY debe ser una clave Fernet válida") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL inválido")
        return value

    @field_validator("crop_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("CROP_TIMEZONE inválida") from exc
        return value

    @model_validator(mode="after")
    def consistent_limits(self) -> "Settings":
        if self.nonce_ttl < 2 * self.hmac_timestamp_tolerance:
            raise ValueError("NONCE_TTL debe duplicar la tolerancia HMAC")
        if self.min_surcos_por_toma > self.max_surcos_por_toma:
            raise ValueError("Rango de surcos inconsistente")
        if self.min_longitud_surco_metros > self.max_longitud_surco_metros:
            raise ValueError("Rango de longitud de surcos inconsistente")
        if self.max_image_bytes > self.max_upload_total_bytes:
            raise ValueError("MAX_IMAGE_BYTES no puede superar el total de carga")
        if self.enforce_https and self.db_echo:
            raise ValueError("DB_ECHO debe estar desactivado al exigir HTTPS")
        storage = self.image_storage_root.expanduser().resolve()
        staging = self.image_staging_root.expanduser().resolve()
        if staging == storage or storage not in staging.parents:
            raise ValueError("IMAGE_STAGING_ROOT debe estar dentro de IMAGE_STORAGE_ROOT")
        formats = self.allowed_formats
        if not formats or not formats <= {"JPEG", "PNG"}:
            raise ValueError("ALLOWED_IMAGE_FORMATS solo admite JPEG y PNG")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def allowed_formats(self) -> set[str]:
        return {item.strip().upper() for item in self.allowed_image_formats.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()

