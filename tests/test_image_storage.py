import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.core.errors import ApiError
from app.services.image_storage import ImageStorage


def image_bytes(image_format="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(output, format=image_format)
    output.seek(0)
    return output


def storage(tmp_path, max_bytes=1024 * 1024):
    root = tmp_path / "images"
    return ImageStorage(root, root / ".staging", max_image_bytes=max_bytes, max_pixels=1000000, allowed_formats={"JPEG", "PNG"})


def test_stage_detects_real_format_and_promotes_safely(tmp_path):
    service = storage(tmp_path)
    staged = service.stage(image_bytes("JPEG"), "leaf.jpg")
    relative = service.build_relative_path(
        capture_date=datetime(2026, 11, 19, tzinfo=timezone.utc),
        terreno_id=1,
        numero_toma=1,
        toma_id=uuid.uuid4(),
        numero_surco=1,
        surco_id=uuid.uuid4(),
        category="con_mildiu",
        image_id=uuid.uuid4(),
        extension=staged.extension,
    )
    assert not relative.startswith("/")
    service.promote(staged.token, relative)
    assert service.final_exists(relative)
    assert not service.staging_exists(staged.token)


def test_rejects_extension_mismatch_and_arbitrary_file(tmp_path):
    service = storage(tmp_path)
    with pytest.raises(ApiError) as mismatch:
        service.stage(image_bytes("PNG"), "leaf.jpg")
    assert mismatch.value.code == "image_extension_mismatch"
    assert not list(service.staging_root.iterdir())
    with pytest.raises(ApiError) as arbitrary:
        service.stage(io.BytesIO(b"not-an-image"), "leaf.jpg")
    assert arbitrary.value.code == "invalid_image"


def test_rejects_size_limit_and_path_traversal(tmp_path):
    service = storage(tmp_path, max_bytes=10)
    with pytest.raises(ApiError) as too_large:
        service.stage(image_bytes(), "leaf.jpg")
    assert too_large.value.status_code == 413
    with pytest.raises(ValueError):
        service.final_exists("../secret")


def test_server_names_do_not_reuse_original_name(tmp_path):
    service = storage(tmp_path)
    staged = service.stage(image_bytes(), "../../same-name.jpg")
    assert staged.original_name == "same-name.jpg"
    assert staged.token != staged.original_name

