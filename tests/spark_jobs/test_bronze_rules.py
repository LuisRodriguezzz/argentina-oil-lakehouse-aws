"""Tests de las reglas puras del job bronze (no requieren Spark ni JVM)."""

from __future__ import annotations

import pytest

from pipelines.spark_jobs.bronze_rules import (
    LandedFile,
    clean_column_name,
    dataset_names,
    landing_uri,
    latest_ok_query,
    load_table_rules,
    namespace_of,
    pending_files,
    postgres_jdbc,
    resources_by_table,
    table_for_resource,
    unmapped_resources,
    with_suffix,
)

PRODUCCION_RULES = load_table_rules("produccion_pozo")

# Nombres tal cual los publica el portal (verificados con `ingest list`).
DDJJ_2024 = "Producción de Pozos de Gas y Petróleo - 2024 (DDJJ abiertas y cerradas)"
NO_CONVENCIONAL = "Producción de Pozos de Gas y Petróleo No Convencional"
CAPITULO_IV = "Capítulo IV - Pozos"
PADRON = "Padrón de Pozos de Capítulo IV con fecha de primera producción"


def landed(resource_id: str, sha256: str, resource_name: str | None = None) -> LandedFile:
    return LandedFile(
        resource_id=resource_id,
        resource_name=resource_name or f"recurso {resource_id}",
        landing_key=f"energia/produccion_pozo/resource_id={resource_id}/x.csv",
        sha256=sha256,
        ingest_date="2026-09-05",
    )


def test_landing_uri_arma_la_ruta_del_objeto():
    assert landing_uri("lakehouse-123", "landing/energia/x.csv") == (
        "s3://lakehouse-123/landing/energia/x.csv"
    )


def test_landing_uri_no_duplica_la_barra_inicial():
    assert landing_uri("lakehouse-123", "/landing/x.csv") == "s3://lakehouse-123/landing/x.csv"


def test_clean_column_name_quita_el_bom():
    assert clean_column_name("\ufeffidempresa") == "idempresa"


def test_clean_column_name_quita_espacios():
    assert clean_column_name("  anio ") == "anio"


def test_clean_column_name_deja_intacto_un_nombre_normal():
    assert clean_column_name("prod_pet") == "prod_pet"


def test_namespace_of():
    assert namespace_of("lake.bronze.produccion_pozo") == "lake.bronze"


# --- sufijo de ambiente (ADR 0005) -----------------------------------------------------


def test_with_suffix_solo_toca_el_namespace():
    assert with_suffix("lake.bronze.fractura", "_dev") == "lake.bronze_dev.fractura"


def test_with_suffix_vacio_deja_la_tabla_igual():
    assert with_suffix("lake.bronze.fractura", "") == "lake.bronze.fractura"


def test_load_table_rules_aplica_el_sufijo_a_todas_las_tablas():
    reglas = load_table_rules("produccion_pozo", suffix="_prod")
    assert all(regla.table.startswith("lake.bronze_prod.") for regla in reglas)


def test_pending_files_incluye_recursos_nuevos():
    files = [landed("a", "sha-a"), landed("b", "sha-b")]
    assert pending_files(files, {"a": "sha-a"}) == [files[1]]


def test_pending_files_incluye_recursos_con_hash_cambiado():
    files = [landed("a", "sha-nueva")]
    assert pending_files(files, {"a": "sha-vieja"}) == files


def test_pending_files_vacio_si_todo_esta_cargado():
    files = [landed("a", "sha-a"), landed("b", "sha-b")]
    assert pending_files(files, {"a": "sha-a", "b": "sha-b"}) == []


def test_latest_ok_query_filtra_dataset_y_status():
    query = latest_ok_query("produccion_pozo")
    assert "dataset = 'produccion_pozo'" in query
    assert "status = 'ok'" in query
    assert "DISTINCT ON (resource_id)" in query


def test_latest_ok_query_rechaza_un_dataset_raro():
    with pytest.raises(ValueError):
        latest_ok_query("produccion'; DROP TABLE ingestion_manifest; --")


def test_postgres_jdbc_traduce_el_dsn():
    url, properties = postgres_jdbc("postgresql://lakehouse:secreto@postgres:5432/lakehouse")
    assert url == "jdbc:postgresql://postgres:5432/lakehouse"
    assert properties["user"] == "lakehouse"
    assert properties["password"] == "secreto"
    assert properties["driver"] == "org.postgresql.Driver"


def test_postgres_jdbc_conserva_el_sslmode():
    # Neon exige TLS; el driver JDBC lo entiende como sslmode y no lee el resto del DSN.
    url, _ = postgres_jdbc(
        "postgresql://u:p@ep-x.neon.tech/lakehouse?sslmode=require&channel_binding=require"
    )
    assert url == "jdbc:postgresql://ep-x.neon.tech:5432/lakehouse?sslmode=require"


def test_postgres_jdbc_asume_el_puerto_por_defecto():
    url, _ = postgres_jdbc("postgresql://u:p@localhost/lakehouse")
    assert url == "jdbc:postgresql://localhost:5432/lakehouse"


def test_dataset_names_son_los_del_yaml():
    assert dataset_names() == ["fractura", "produccion_pozo"]


def test_load_table_rules_falla_con_un_dataset_desconocido():
    with pytest.raises(KeyError):
        load_table_rules("reservas")


def test_los_anuales_de_ddjj_van_a_produccion_pozo():
    assert table_for_resource(PRODUCCION_RULES, DDJJ_2024) == "lake.bronze.produccion_pozo"


def test_no_convencional_va_a_su_propia_tabla():
    # Es un subconjunto de los anuales: en la misma tabla duplicaria filas.
    assert (
        table_for_resource(PRODUCCION_RULES, NO_CONVENCIONAL)
        == "lake.bronze.produccion_pozo_no_convencional"
    )


def test_capitulo_iv_va_al_catalogo_de_pozos():
    assert table_for_resource(PRODUCCION_RULES, CAPITULO_IV) == "lake.bronze.pozo_catalogo"


def test_el_padron_va_a_primera_produccion():
    assert table_for_resource(PRODUCCION_RULES, PADRON) == "lake.bronze.pozo_primera_produccion"


def test_un_recurso_desconocido_no_tiene_tabla():
    assert table_for_resource(PRODUCCION_RULES, "Agrupado por yacimiento") is None


def test_cualquier_recurso_de_fractura_va_a_la_misma_tabla():
    rules = load_table_rules("fractura")
    assert table_for_resource(rules, "Datos de fractura 2024") == "lake.bronze.fractura"


def test_resources_by_table_agrupa_por_destino():
    files = [
        landed("a", "sha-a", DDJJ_2024),
        landed("b", "sha-b", NO_CONVENCIONAL),
        landed("c", "sha-c", PADRON),
        landed("d", "sha-d", "Agrupado por yacimiento"),
    ]
    grupos = resources_by_table(files, PRODUCCION_RULES)
    assert sorted(grupos) == [
        "lake.bronze.pozo_primera_produccion",
        "lake.bronze.produccion_pozo",
        "lake.bronze.produccion_pozo_no_convencional",
    ]
    assert grupos["lake.bronze.produccion_pozo"] == [files[0]]


def test_unmapped_resources_devuelve_los_que_se_saltean():
    files = [landed("a", "sha-a", DDJJ_2024), landed("d", "sha-d", "Agrupado por yacimiento")]
    assert unmapped_resources(files, PRODUCCION_RULES) == [files[1]]
