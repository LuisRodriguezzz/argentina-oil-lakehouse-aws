"""El cliente CKAN parsea package_show y fuerza http en las URLs del portal."""

from __future__ import annotations

import pytest
import responses

from pipelines.ingest.ckan import CkanClient, force_http

from .conftest import CKAN_BASE

PACKAGE_SHOW = {
    "success": True,
    "result": {
        "resources": [
            {
                "id": "0a352dee-8b4e-4e95-b01e-5b8082ce22ac",
                "name": "Producción de Pozos de Gas y Petróleo - 2024 (DDJJ abiertas y cerradas)",
                "url": "https://datos.energia.gob.ar/dataset/x/resource/0a35/download/prod-2024.csv",
                "format": "CSV",
                "size": 307588401,
                "last_modified": "2026-08-04T08:02:39.279372",
                "datastore_active": True,
            },
            {
                "id": "3fcda0c5-68aa-4f33-bbe2-0180e6dbeebe",
                "name": "Capítulo IV - Pozos",
                "url": "http://datos.energia.gob.ar/dataset/x/resource/3fcd/download/cap4.zip",
                "format": "shp",
                "size": "4146975",
                "last_modified": None,
                "created": "2019-01-09T13:08:07.908832",
            },
        ]
    },
}


@responses.activate
def test_package_show_parsea_y_normaliza():
    responses.add(
        responses.GET,
        f"{CKAN_BASE}/api/3/action/package_show",
        json=PACKAGE_SHOW,
        status=200,
    )
    resources = CkanClient(CKAN_BASE).package_show("produccion-de-petroleo-y-gas-por-pozo")

    assert len(resources) == 2
    first = resources[0]
    assert first.url.startswith("http://")  # el portal redirige https -> http
    assert first.size == 307588401
    assert first.format == "CSV"
    assert first.datastore_active is True
    assert first.filename == "prod-2024.csv"

    second = resources[1]
    assert second.format == "SHP"
    assert second.size == 4146975  # llega como string y se normaliza
    assert second.last_modified == "2019-01-09T13:08:07.908832"  # cae a created
    assert second.datastore_active is False


@responses.activate
def test_package_show_lanza_si_ckan_marca_error():
    responses.add(
        responses.GET,
        f"{CKAN_BASE}/api/3/action/package_show",
        json={"success": False, "error": {"message": "Not found"}},
        status=200,
    )
    with pytest.raises(RuntimeError):
        CkanClient(CKAN_BASE).package_show("no-existe")


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://datos.energia.gob.ar/a", "http://datos.energia.gob.ar/a"),
        ("https://www.energia.gob.ar/b.zip", "http://www.energia.gob.ar/b.zip"),
        ("http://datos.energia.gob.ar/a", "http://datos.energia.gob.ar/a"),
        ("https://otro.dominio.com/a", "https://otro.dominio.com/a"),
    ],
)
def test_force_http_solo_toca_el_portal(url, esperado):
    assert force_http(url) == esperado


def test_el_base_url_se_normaliza_a_http():
    assert CkanClient("https://datos.energia.gob.ar/").base_url == "http://datos.energia.gob.ar"
