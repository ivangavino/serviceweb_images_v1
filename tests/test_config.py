from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


def settings(tmp_path: Path, **changes):
    base = {
        "db_host": "localhost",
        "db_port": 5432,
        "db_name": "test",
        "db_user": "test",
        "db_password": "secret",
        "hmac_master_key": Fernet.generate_key().decode(),
        "image_storage_root": tmp_path / "images",
        "image_staging_root": tmp_path / "images" / ".staging",
    }
    base.update(changes)
    return Settings(**base)


def test_valid_settings_and_escaped_database_url(tmp_path):
    value = settings(tmp_path, db_password="p@ss:/word")
    assert value.database_url.password == "p@ss:/word"


@pytest.mark.parametrize(
    "change",
    [
        {"nonce_ttl": 599, "hmac_timestamp_tolerance": 300},
        {"min_surcos_por_toma": 6, "max_surcos_por_toma": 5},
        {"allowed_image_formats": "GIF"},
    ],
)
def test_rejects_inconsistent_settings(tmp_path, change):
    with pytest.raises(ValidationError):
        settings(tmp_path, **change)


def test_staging_must_be_inside_storage(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, image_staging_root=tmp_path / "outside")

