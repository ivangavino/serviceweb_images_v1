import hashlib
from pathlib import Path


BASE_SCHEMA_SHA256 = "953da60f7b83471c665786a01e60c76b5a2cef4ed37bc8cfe8b2f4902ac5c9dc"


def test_manual_schema_script_is_transactional_and_complete():
    sql = Path("script.sql").read_text(encoding="utf-8")
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "CREATE EXTENSION" not in sql
    assert "ALTER TABLE public.terrenos" not in sql
    assert "SCRIPT COMPLEMENTARIO DE: esquema_quinua_iot.sql" in sql
    expected_tables = {
        "prototipos_mildiu",
        "credenciales_prototipo_mildiu",
        "prototipos_terrenos_mildiu",
        "nonces_mildiu",
        "tomas_mildiu",
        "surcos_mildiu",
        "resultados_mildiu",
        "lotes_imagenes_mildiu",
        "imagenes_mildiu",
    }
    for table in expected_tables:
        assert f"CREATE TABLE public.{table}" in sql


def test_production_base_schema_remains_unchanged():
    content = Path("esquema_quinua_iot.sql").read_bytes()
    assert hashlib.sha256(content).hexdigest() == BASE_SCHEMA_SHA256


def test_application_does_not_create_schema_automatically():
    app_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("app").rglob("*.py")
    )
    assert "create_all(" not in app_sources
