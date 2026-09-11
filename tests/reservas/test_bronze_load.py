"""Idempotencia y linaje del bronze de reservas (las partes que no tocan S3 ni Iceberg)."""

from __future__ import annotations

from datetime import date, datetime

import pyarrow as pa

from pipelines.reservas.bronze_load import (
    LandedZip,
    bronze_schema,
    pending_files,
    to_arrow,
    with_lineage,
)
from pipelines.reservas.parser import LONG_COLUMNS


def landed(resource_id: str, sha256: str) -> LandedZip:
    return LandedZip(
        resource_id=resource_id,
        resource_name=f"reservas_al_31-12-{resource_id}.zip",
        landing_key=f"energia/reservas/resource_id={resource_id}/x.zip",
        sha256=sha256,
        ingest_date=date(2026, 9, 5),
    )


def test_un_recurso_nuevo_esta_pendiente() -> None:
    archivos = [landed("2024", "aaa")]
    assert pending_files(archivos, {}) == archivos


def test_un_recurso_con_el_mismo_sha_no_se_recarga() -> None:
    assert pending_files([landed("2024", "aaa")], {"2024": "aaa"}) == []


def test_un_recurso_con_sha_distinto_se_recarga() -> None:
    archivos = [landed("2024", "bbb")]
    assert pending_files(archivos, {"2024": "aaa"}) == archivos


def test_solo_se_recarga_el_que_cambio() -> None:
    viejo, nuevo = landed("2023", "aaa"), landed("2024", "ccc")
    assert pending_files([viejo, nuevo], {"2023": "aaa", "2024": "bbb"}) == [nuevo]


def test_el_linaje_dice_de_donde_salio_la_fila() -> None:
    archivo = landed("2024", "aaa")
    momento = datetime(2026, 9, 5, 12, 0, 0)
    fila = with_lineage([{"operador": "YPF S.A."}], archivo, momento)[0]
    assert fila["operador"] == "YPF S.A."
    assert fila["_resource_id"] == "2024"
    assert fila["_source_key"] == archivo.landing_key
    assert fila["_source_sha256"] == "aaa"
    assert fila["_ingest_date"] == date(2026, 9, 5)
    assert fila["_loaded_at"] == momento
    assert fila["data_origin"] == "real"


def test_el_esquema_de_bronze_no_tipa_los_datos() -> None:
    """Todo string salvo las dos marcas de tiempo: el tipado es responsabilidad de silver."""
    campos = {campo.name: str(campo.field_type) for campo in bronze_schema().fields}
    assert campos["valor"] == "string"
    assert campos["anio_corte"] == "string"
    assert campos["_ingest_date"] == "date"
    assert campos["_loaded_at"] == "timestamp"


def test_las_filas_se_arman_con_el_esquema_de_la_tabla() -> None:
    """El Arrow tiene que salir con el esquema de Iceberg, que trae los ids de campo."""
    archivo = landed("2024", "aaa")
    filas = with_lineage(
        [dict.fromkeys(LONG_COLUMNS, "x")], archivo, datetime(2026, 9, 5, 12, 0, 0)
    )
    tabla = to_arrow(filas, bronze_schema().as_arrow())
    assert tabla.num_rows == 1
    assert tabla.column("operador").to_pylist() == ["x"]
    assert tabla.column("_ingest_date").type == pa.date32()
