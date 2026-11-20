"""Optional checks that must run only against a dedicated PostgreSQL/PostGIS DB."""

import os

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def postgis_connection():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated migrated PostgreSQL/PostGIS database")
    engine = create_engine(url, hide_parameters=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def test_postgis_distance_and_required_tables(postgis_connection):
    distance = postgis_connection.execute(
        text(
            "SELECT ST_Length(ST_MakeLine("
            "ST_SetSRID(ST_MakePoint(-76.9501,-12.1001),4326),"
            "ST_SetSRID(ST_MakePoint(-76.9503,-12.1007),4326))::geography)"
        )
    ).scalar_one()
    assert 60 < distance < 80
    for table in ("prototipos_mildiu", "tomas_mildiu", "surcos_mildiu", "resultados_mildiu", "imagenes_mildiu"):
        assert postgis_connection.execute(text("SELECT to_regclass(:name)"), {"name": table}).scalar_one() == table

