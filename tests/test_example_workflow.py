import json

import pytest

from examples.enviar_prueba_completa import (
    WorkflowError,
    build_payload,
    required_environment,
)


def take_data():
    return {
        "client_toma_id": "4e539f17-4038-4145-b11f-6241b87ac02d",
        "terreno_id": 1,
        "fecha_inicio": "2026-11-25T10:00:00-05:00",
        "fecha_fin": "2026-11-25T10:35:00-05:00",
        "numero_toma_semana": 1,
        "modelo_ia": "QuinuAlert-Mildiu",
        "version_modelo": "1.0.0",
        "surcos": [
            {
                "numero_surco": 1,
                "codigo_surco": "SURCO-01",
                "latitud_inicio": -12.1007,
                "longitud_inicio": -76.9509,
                "latitud_fin": -12.1007,
                "longitud_fin": -76.9512,
                "imagenes": [
                    {"categoria": "con_mildiu", "archivo": "s1-enferma.jpg"},
                    {"categoria": "sin_mildiu", "archivo": "s1-sana.jpg"},
                ],
            },
            {
                "numero_surco": 2,
                "codigo_surco": "SURCO-02",
                "latitud_inicio": -12.1010,
                "longitud_inicio": -76.9513,
                "latitud_fin": -12.1010,
                "longitud_fin": -76.9516,
                "imagenes": [
                    {"categoria": "posible_mildiu", "archivo": "s2-posible.jpg"}
                ],
            },
        ],
    }


def test_builds_independent_image_batches_per_furrow(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for filename in ("s1-enferma.jpg", "s1-sana.jpg", "s2-posible.jpg"):
        (image_dir / filename).write_bytes(filename.encode())
    template = tmp_path / "take.json"
    template.write_text(json.dumps(take_data()), encoding="utf-8")

    body, payload, images_by_furrow = build_payload(template, image_dir, 2)

    assert payload.numero_toma_semana == 2
    assert payload.surcos[0].resultados.imagenes_con_mildiu == 1
    assert payload.surcos[0].resultados.imagenes_sin_mildiu == 1
    assert payload.surcos[1].resultados.imagenes_posible_mildiu == 1
    assert [path.name for _, path in images_by_furrow["SURCO-01"]] == [
        "s1-enferma.jpg",
        "s1-sana.jpg",
    ]
    assert [path.name for _, path in images_by_furrow["SURCO-02"]] == [
        "s2-posible.jpg"
    ]
    assert all("imagenes" not in furrow for furrow in json.loads(body)["surcos"])


def test_rejects_an_image_outside_image_directory(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (tmp_path / "outside.jpg").write_bytes(b"outside")
    data = take_data()
    data["surcos"][0]["imagenes"][0]["archivo"] = "../outside.jpg"
    template = tmp_path / "take.json"
    template.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(WorkflowError, match="fuera de --image-dir"):
        build_payload(template, image_dir, None)


def test_requires_client_credentials_from_environment(monkeypatch):
    for name in (
        "QUINUALERT_DEVICE_ID",
        "QUINUALERT_KEY_ID",
        "QUINUALERT_DEVICE_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(WorkflowError, match="Faltan variables de entorno"):
        required_environment()
