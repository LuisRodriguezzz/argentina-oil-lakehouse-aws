"""El manifiesto en SQLite: start, finish y latest_ok."""

from __future__ import annotations

from datetime import date

from pipelines.ingest.manifest import STATUS_OK, STATUS_UNCHANGED, Manifest

DIA = date(2026, 9, 5)


def _start(manifest: Manifest, resource_id: str = "r1", size: int = 100) -> int:
    return manifest.start(
        dataset="produccion_pozo",
        source_type="ckan",
        resource_id=resource_id,
        resource_name="Prod 2024",
        url="http://datos.energia.gob.ar/x.csv",
        size_bytes_source=size,
        last_modified_source="2026-08-04T08:02:39",
        ingest_date=DIA,
        landing_key="energia/produccion_pozo/resource_id=r1/ingest_date=2026-09-05/x.csv",
    )


def test_start_deja_la_fila_abierta_como_failed(manifest):
    run_id = _start(manifest)
    fila = manifest.recent("produccion_pozo")[0]
    assert fila["id"] == run_id
    assert fila["status"] == "failed"
    assert fila["finished_at"] is None
    assert manifest.latest_ok("produccion_pozo", "r1") is None


def test_finish_ok_y_latest_ok(manifest):
    run_id = _start(manifest)
    manifest.finish_ok(run_id, sha256="abc", size_bytes_landed=100, landing_key="k")

    fila = manifest.latest_ok("produccion_pozo", "r1")
    assert fila is not None
    assert fila["status"] == STATUS_OK
    assert fila["sha256"] == "abc"
    assert fila["size_bytes_landed"] == 100
    assert fila["landing_key"] == "k"
    assert fila["finished_at"] is not None


def test_finish_failed_guarda_el_error(manifest):
    run_id = _start(manifest)
    manifest.finish_failed(run_id, "HTTPError: 500")

    fila = manifest.recent("produccion_pozo")[0]
    assert fila["status"] == "failed"
    assert "500" in fila["error"]
    assert manifest.latest_ok("produccion_pozo", "r1") is None


def test_latest_ok_ignora_unchanged_y_devuelve_la_ultima_descarga(manifest):
    primera = _start(manifest)
    manifest.finish_ok(primera, sha256="v1", size_bytes_landed=100, landing_key="k1")
    segunda = _start(manifest, size=200)
    manifest.finish_ok(segunda, sha256="v2", size_bytes_landed=200, landing_key="k2")
    tercera = _start(manifest, size=200)
    manifest.finish_ok(
        tercera, sha256="v2", size_bytes_landed=200, landing_key="k2", status=STATUS_UNCHANGED
    )

    fila = manifest.latest_ok("produccion_pozo", "r1")
    assert fila["id"] == segunda
    assert fila["sha256"] == "v2"


def test_latest_ok_aisla_por_dataset_y_recurso(manifest):
    run_id = _start(manifest, resource_id="r1")
    manifest.finish_ok(run_id, sha256="a", size_bytes_landed=1, landing_key="k")
    assert manifest.latest_ok("produccion_pozo", "r2") is None
    assert manifest.latest_ok("otro_dataset", "r1") is None


def test_recent_ordena_por_id_descendente(manifest):
    ids = [_start(manifest, resource_id=f"r{i}") for i in range(3)]
    filas = manifest.recent("produccion_pozo", limit=2)
    assert [f["id"] for f in filas] == list(reversed(ids))[:2]
