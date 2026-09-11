"""Runner end-to-end con CKAN mockeado (responses), landing en Moto y manifiesto en SQLite."""

from __future__ import annotations

import hashlib

import responses

from pipelines.ingest.ckan import CkanClient
from pipelines.ingest.runner import run

from .conftest import BUCKET, CKAN_BASE

PACKAGE_URL = f"{CKAN_BASE}/api/3/action/package_show"
CSV_2024 = f"{CKAN_BASE}/dataset/x/resource/r2024/download/prod-2024.csv"
CSV_NOCONV = f"{CKAN_BASE}/dataset/x/resource/rnc/download/no-convencional.csv"

CONTENIDO_2024 = b"idpozo,anio,mes\n1,2024,1\n" * 500
CONTENIDO_NOCONV = b"idpozo,anio,mes\n2,2024,1\n" * 300


def _package(last_modified_2024: str = "2026-08-04T08:02:39", size_2024: int | None = None):
    return {
        "success": True,
        "result": {
            "resources": [
                {
                    "id": "r2024",
                    "name": "Producción de Pozos de Gas y Petróleo - 2024 "
                    "(DDJJ abiertas y cerradas)",
                    "url": CSV_2024,
                    "format": "CSV",
                    "size": size_2024 if size_2024 is not None else len(CONTENIDO_2024),
                    "last_modified": last_modified_2024,
                },
                {
                    "id": "rnc",
                    "name": "Producción de Pozos de Gas y Petróleo No Convencional",
                    "url": CSV_NOCONV,
                    "format": "CSV",
                    "size": len(CONTENIDO_NOCONV),
                    "last_modified": "2026-08-22T21:00:52",
                },
                {
                    # mismo nombre que un CSV incluido pero en SHP: no debe entrar
                    "id": "rshp",
                    "name": "Capítulo IV - Pozos",
                    "url": f"{CKAN_BASE}/dataset/x/resource/rshp/download/cap4.zip",
                    "format": "SHP",
                    "size": 10,
                    "last_modified": "2026-08-21T05:00:09",
                },
                {
                    # familia normal del mismo año: excluida por include
                    "id": "r2024-normal",
                    "name": "Producción de Pozos de Gas y Petróleo - 2024",
                    "url": f"{CKAN_BASE}/dataset/x/resource/r2024n/download/prod-2024n.csv",
                    "format": "CSV",
                    "size": 999,
                    "last_modified": "2026-03-03T08:01:51",
                },
            ]
        },
    }


def _registrar_paquete(paquete: dict) -> None:
    responses.add(responses.GET, PACKAGE_URL, json=paquete, status=200)


def _registrar_descargas() -> None:
    responses.add(responses.GET, CSV_2024, body=CONTENIDO_2024, status=200)
    responses.add(responses.GET, CSV_NOCONV, body=CONTENIDO_NOCONV, status=200)


def _correr(spec, manifest, storage, **kwargs):
    return run(spec, manifest=manifest, storage=storage, ckan=CkanClient(CKAN_BASE), **kwargs)


def _gets_de_archivos() -> list[str]:
    return [c.request.url for c in responses.calls if "/download/" in c.request.url]


@responses.activate
def test_primera_corrida_deja_todo_ok(specs, manifest, storage, s3_client):
    _registrar_paquete(_package())
    _registrar_descargas()

    resumen = _correr(specs["produccion_pozo"], manifest, storage)

    assert (resumen.ok, resumen.unchanged, resumen.failed) == (2, 0, 0)
    assert resumen.exit_code == 0
    nombres = {i.resource_id for i in resumen.items}
    assert nombres == {"r2024", "rnc"}  # el SHP y la familia normal quedaron afuera

    item = next(i for i in resumen.items if i.resource_id == "r2024")
    assert item.sha256 == hashlib.sha256(CONTENIDO_2024).hexdigest()
    assert item.landing_key.startswith("energia/produccion_pozo/resource_id=r2024/ingest_date=")
    cuerpo = s3_client.get_object(Bucket=BUCKET, Key=item.landing_key)["Body"].read()
    assert cuerpo == CONTENIDO_2024


@responses.activate
def test_segunda_corrida_sin_cambios_no_descarga(specs, manifest, storage):
    _registrar_paquete(_package())
    _registrar_descargas()
    _correr(specs["produccion_pozo"], manifest, storage)
    descargas_primera = len(_gets_de_archivos())

    resumen = _correr(specs["produccion_pozo"], manifest, storage)

    assert (resumen.ok, resumen.unchanged, resumen.failed) == (0, 2, 0)
    assert all(i.downloaded is False for i in resumen.items)
    assert len(_gets_de_archivos()) == descargas_primera  # no hubo GET del archivo


@responses.activate
def test_cambio_de_last_modified_con_mismo_contenido_da_unchanged(specs, manifest, storage):
    _registrar_paquete(_package())
    _registrar_descargas()
    _correr(specs["produccion_pozo"], manifest, storage)

    responses.reset()
    _registrar_paquete(_package(last_modified_2024="2026-09-01T10:00:00"))
    _registrar_descargas()
    resumen = _correr(specs["produccion_pozo"], manifest, storage)

    item = next(i for i in resumen.items if i.resource_id == "r2024")
    assert item.status == "unchanged"
    assert item.downloaded is True  # hubo que bajarlo para comparar el hash
    assert _gets_de_archivos() == [CSV_2024]
    assert resumen.unchanged == 2 and resumen.ok == 0


@responses.activate
def test_contenido_distinto_vuelve_a_ok(specs, manifest, storage):
    _registrar_paquete(_package())
    _registrar_descargas()
    _correr(specs["produccion_pozo"], manifest, storage)

    nuevo = CONTENIDO_2024 + b"1,2024,2\n"
    responses.reset()
    _registrar_paquete(_package(last_modified_2024="2026-09-01T10:00:00", size_2024=len(nuevo)))
    responses.add(responses.GET, CSV_2024, body=nuevo, status=200)
    responses.add(responses.GET, CSV_NOCONV, body=CONTENIDO_NOCONV, status=200)
    resumen = _correr(specs["produccion_pozo"], manifest, storage)

    item = next(i for i in resumen.items if i.resource_id == "r2024")
    assert item.status == "ok"
    assert item.sha256 == hashlib.sha256(nuevo).hexdigest()


@responses.activate
def test_un_recurso_roto_no_corta_la_corrida(specs, manifest, storage):
    _registrar_paquete(_package())
    responses.add(responses.GET, CSV_2024, body="boom", status=500)
    responses.add(responses.GET, CSV_NOCONV, body=CONTENIDO_NOCONV, status=200)

    resumen = _correr(specs["produccion_pozo"], manifest, storage)

    assert (resumen.ok, resumen.failed) == (1, 1)
    assert resumen.exit_code == 1
    fallido = next(i for i in resumen.items if i.status == "failed")
    assert fallido.resource_id == "r2024"
    fila = next(f for f in manifest.recent("produccion_pozo") if f["resource_id"] == "r2024")
    assert fila["status"] == "failed" and "500" in fila["error"]
    # el objeto roto no quedo en landing
    assert storage.object_size(fila["landing_key"]) is None


@responses.activate
def test_only_filtra_por_nombre(specs, manifest, storage):
    _registrar_paquete(_package())
    _registrar_descargas()

    resumen = _correr(specs["produccion_pozo"], manifest, storage, only="2024")

    assert [i.resource_id for i in resumen.items] == ["r2024"]


@responses.activate
def test_dry_run_no_descarga_ni_escribe_manifiesto(specs, manifest, storage):
    _registrar_paquete(_package())
    _registrar_descargas()

    resumen = _correr(specs["produccion_pozo"], manifest, storage, dry_run=True)

    assert len(resumen.items) == 2
    assert _gets_de_archivos() == []
    assert manifest.recent("produccion_pozo") == []


@responses.activate
def test_fuente_http_file_usa_head_y_hash_de_url(specs, manifest, storage, s3_client):
    url = "http://www.energia.gob.ar/reservas_al_31-12-2024.zip"
    contenido = b"PK\x03\x04" + b"x" * 2048
    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "Content-Length": str(len(contenido)),
            "Last-Modified": "Tue, 09 Sep 2025 14:43:43 GMT",
        },
    )
    responses.add(responses.GET, url, body=contenido, status=200)

    resumen = run(specs["reservas"], manifest=manifest, storage=storage)

    assert resumen.ok == 1
    item = resumen.items[0]
    assert item.resource_name == "reservas_al_31-12-2024.zip"
    assert item.sha256 == hashlib.sha256(contenido).hexdigest()
    assert s3_client.get_object(Bucket=BUCKET, Key=item.landing_key)["Body"].read() == contenido

    resumen2 = run(specs["reservas"], manifest=manifest, storage=storage)
    assert resumen2.unchanged == 1
    assert resumen2.items[0].downloaded is False
