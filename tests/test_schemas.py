import uuid

import pytest
from pydantic import ValidationError

from app.schemas.mildiu import ResultadoCreate, TomaCreate, geographic_length_meters


def valid_result(**changes):
    data = {
        "total_imagenes": 4,
        "imagenes_procesadas": 3,
        "imagenes_con_mildiu": 1,
        "imagenes_sin_mildiu": 1,
        "imagenes_posible_mildiu": 1,
        "imagenes_no_procesadas": 1,
    }
    data.update(changes)
    return data


def valid_take(**changes):
    data = {
        "client_toma_id": str(uuid.uuid4()),
        "terreno_id": 1,
        "fecha_inicio": "2026-11-19T09:00:00-05:00",
        "fecha_fin": "2026-11-19T10:00:00-05:00",
        "numero_toma_semana": 1,
        "modelo_ia": "QuinuAlert-Mildiu",
        "version_modelo": "1.0.0",
        "surcos": [
            {
                "numero_surco": 1,
                "codigo_surco": "SURCO-01",
                "latitud_inicio": -12.1001,
                "longitud_inicio": -76.9501,
                "latitud_fin": -12.1007,
                "longitud_fin": -76.9503,
                "resultados": valid_result(),
            }
        ],
    }
    data.update(changes)
    return data


def test_valid_take_and_stable_hash():
    take = TomaCreate.model_validate(valid_take())
    assert len(take.surcos) == 1
    assert take.content_hash() == take.content_hash()


def test_accepts_multiple_furrows_in_one_take():
    furrow = valid_take()["surcos"][0]
    take = TomaCreate.model_validate(
        valid_take(
            surcos=[
                furrow,
                furrow
                | {
                    "numero_surco": 2,
                    "codigo_surco": "SURCO-02",
                    "latitud_inicio": -12.1008,
                    "latitud_fin": -12.1014,
                },
            ]
        )
    )
    assert [item.numero_surco for item in take.surcos] == [1, 2]


@pytest.mark.parametrize(
    "changes",
    [
        {"imagenes_procesadas": 2},
        {"total_imagenes": 2},
        {"imagenes_no_procesadas": 0},
        {"imagenes_con_mildiu": -1},
    ],
)
def test_rejects_inconsistent_results(changes):
    with pytest.raises(ValidationError):
        ResultadoCreate.model_validate(valid_result(**changes))


def test_rejects_more_than_twenty_at_contract_boundary():
    furrow = valid_take()["surcos"][0]
    with pytest.raises(ValidationError):
        TomaCreate.model_validate(valid_take(surcos=[furrow | {"numero_surco": i, "codigo_surco": f"S-{i}"} for i in range(1, 22)]))


def test_rejects_duplicate_furrows_and_bad_coordinates():
    furrow = valid_take()["surcos"][0]
    with pytest.raises(ValidationError):
        TomaCreate.model_validate(valid_take(surcos=[furrow, furrow]))
    with pytest.raises(ValidationError):
        TomaCreate.model_validate(valid_take(surcos=[furrow | {"latitud_fin": 91}]))


def test_requires_timezone():
    with pytest.raises(ValidationError):
        TomaCreate.model_validate(valid_take(fecha_inicio="2026-11-19T09:00:00"))


def test_geographic_length_is_in_expected_range():
    distance = geographic_length_meters(-12.1001, -76.9501, -12.1007, -76.9503)
    assert 60 < distance < 80

