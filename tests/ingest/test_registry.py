"""El registro carga y filtra segun include, exclude y formatos."""

from __future__ import annotations

import pytest

from pipelines.ingest.registry import DatasetSpec, load_registry


def test_carga_las_entradas_del_yaml(specs):
    assert set(specs) == {"produccion_pozo", "reservas"}
    assert specs["produccion_pozo"].source_type == "ckan"
    assert specs["reservas"].years == (2024,)


def test_include_selecciona_la_familia_ddjj_y_los_agregados(specs):
    spec = specs["produccion_pozo"]
    assert spec.matches(
        "Producción de Pozos de Gas y Petróleo - 2024 (DDJJ abiertas y cerradas)", "CSV"
    )
    assert spec.matches("Producción de Pozos de Gas y Petróleo No Convencional", "CSV")
    assert spec.matches("Capítulo IV - Pozos", "CSV")
    # familia normal: mismo año pero sin el sufijo DDJJ
    assert not spec.matches("Producción de Pozos de Gas y Petróleo - 2024", "CSV")
    assert not spec.matches("Serie histórica de producción de Gas Natural", "CSV")


def test_el_formato_descarta_el_shp_homonimo(specs):
    spec = specs["produccion_pozo"]
    assert spec.matches("Capítulo IV - Pozos", "CSV")
    assert not spec.matches("Capítulo IV - Pozos", "SHP")


def test_exclude_gana_sobre_include():
    spec = DatasetSpec(
        name="x",
        source_type="ckan",
        ckan_package_id="p",
        landing_prefix="p/x",
        include=("Pozos",),
        exclude=("shape",),
    )
    assert spec.matches("Pozos 2024", "CSV")
    assert not spec.matches("Pozos shapefile", "CSV")


def test_include_vacio_acepta_todo():
    spec = DatasetSpec(name="x", source_type="ckan", ckan_package_id="p", landing_prefix="p/x")
    assert spec.matches("cualquier cosa", "PDF")


def test_urls_expande_los_anios(specs):
    assert specs["reservas"].urls() == [
        (2024, "http://www.energia.gob.ar/reservas_al_31-12-2024.zip")
    ]


def test_valida_configuracion_incoherente():
    with pytest.raises(ValueError):
        DatasetSpec(name="x", source_type="ckan", landing_prefix="p/x")
    with pytest.raises(ValueError):
        DatasetSpec(
            name="x",
            source_type="http_file",
            landing_prefix="p/x",
            url_template="http://a/b.zip",
            years=(2024,),
        )
    with pytest.raises(ValueError):
        DatasetSpec(name="x", source_type="otro", landing_prefix="p/x")


def test_el_registro_real_del_repo_es_valido():
    specs = load_registry()
    assert set(specs) == {"produccion_pozo", "fractura", "reservas"}
