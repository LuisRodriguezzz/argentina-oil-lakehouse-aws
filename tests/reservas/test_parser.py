"""El parser desarma el cuadro de Excel a filas largas."""

from __future__ import annotations

import pytest

from pipelines.reservas.parser import (
    LONG_COLUMNS,
    anio_from_name,
    normalize,
    parse_bytes,
    sheet_kind,
)
from tests.reservas.conftest import PRIMERA_FILA_DATOS, celda_esperada

ANIO = 2024
# 2 operadores x 2 hojas x 16 columnas de valor (8 por bloque, sin el bloque derivado).
FILAS_LARGAS = 64


def filas(planilla: bytes) -> list[dict[str, str]]:
    return parse_bytes(planilla, ANIO).rows


def una(planilla: bytes, **filtros: str) -> dict[str, str]:
    """La unica fila larga que cumple todos los filtros."""
    encontradas = [
        fila
        for fila in filas(planilla)
        if all(fila[clave] == valor for clave, valor in filtros.items())
    ]
    assert len(encontradas) == 1, f"{len(encontradas)} filas para {filtros}"
    return encontradas[0]


def test_cantidad_de_filas_largas(planilla: bytes) -> None:
    assert len(filas(planilla)) == FILAS_LARGAS


def test_columnas_de_la_fila_larga(planilla: bytes) -> None:
    assert set(filas(planilla)[0]) == set(LONG_COLUMNS)


def test_las_dos_hojas_se_leen(planilla: bytes) -> None:
    resultado = parse_bytes(planilla, ANIO)
    assert resultado.sheets == ["fin_concesion", "fin_vida_util"]


def test_los_rangos_fusionados_se_propagan_a_la_derecha(planilla: bytes) -> None:
    """La columna 7 (GAS) hereda COMPROBADAS de la 6, que es donde esta el rotulo."""
    fila = una(
        planilla,
        hoja="fin_concesion",
        operador="YPF S.A.",
        tipo_recurso="convencional",
        categoria="reservas",
        certeza="comprobadas",
        fluido="gas",
    )
    assert fila["unidad"] == "MMm3"
    assert fila["valor"] == str(celda_esperada(PRIMERA_FILA_DATOS, 7))


def test_el_rango_fusionado_a_lo_alto_no_inventa_certeza(planilla: bytes) -> None:
    """RECURSOS CONTINGENTES ocupa la fila de categoria y la de certeza: no se subdivide."""
    contingentes = [
        fila for fila in filas(planilla) if fila["categoria"] == "recursos_contingentes"
    ]
    assert len(contingentes) == 16
    assert {fila["certeza"] for fila in contingentes} == {"no_aplica"}


def test_el_bloque_derivado_no_se_carga(planilla: bytes) -> None:
    """`CONVENCIONAL + NO CONVENCIONAL` es la suma de los otros dos, no un dato nuevo."""
    tipos = {fila["tipo_recurso"] for fila in filas(planilla)}
    assert tipos == {"convencional", "no_convencional"}


def test_la_fila_de_total_se_descarta(planilla: bytes) -> None:
    resultado = parse_bytes(planilla, ANIO)
    assert resultado.skipped_totals == 2
    assert all(fila["operador"] != "TOTAL" for fila in resultado.rows)


def test_la_identificacion_llega_completa(planilla: bytes) -> None:
    fila = una(
        planilla,
        hoja="fin_vida_util",
        operador="PAN AMERICAN ENERGY SL",
        tipo_recurso="no_convencional",
        categoria="recursos_contingentes",
        fluido="petroleo",
    )
    assert fila["cuenca"] == "GOLFO SAN JORGE"
    assert fila["provincia"] == "Chubut"
    assert fila["concesion"] == "CERRO DRAGON"
    assert fila["yacimiento"] == "ANTICLINAL FUNES"
    assert fila["anio_corte"] == "2024"
    assert fila["unidad"] == "Mm3"
    # Columna 20: bloque no convencional (base 14) + 6 de recursos contingentes.
    assert fila["valor"] == str(celda_esperada(PRIMERA_FILA_DATOS + 1, 20))


def test_cada_combinacion_aparece_una_sola_vez(planilla: bytes) -> None:
    """La clave del contrato distingue todas las filas largas de un operador."""
    claves = {
        (fila["hoja"], fila["tipo_recurso"], fila["categoria"], fila["certeza"], fila["fluido"])
        for fila in filas(planilla)
        if fila["operador"] == "YPF S.A."
    }
    assert len(claves) == FILAS_LARGAS // 2


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        ("fin de concesión", "fin_concesion"),
        ("Fin Concesion", "fin_concesion"),
        ("fin de vida util", "fin_vida_util"),
        ("Fin de vida útil", "fin_vida_util"),
        ("Hoja1", None),
    ],
)
def test_nombre_de_hoja_tolera_las_variantes_entre_anios(titulo: str, esperado: str | None) -> None:
    assert sheet_kind(titulo) == esperado


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("reservas al 31-12-2024.xlsx", 2024),
        ("reservas_al_31-12-2020.zip", 2020),
        ("RESERVAS AL 31-12-2021.XLSX", 2021),
    ],
)
def test_anio_de_corte_del_nombre(nombre: str, esperado: int) -> None:
    assert anio_from_name(nombre) == esperado


def test_anio_de_corte_sin_anio_falla() -> None:
    with pytest.raises(ValueError, match="anio de corte"):
        anio_from_name("reservas.xlsx")


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [("  CONCESIÓN O  PERMISO ", "CONCESION O PERMISO"), (None, ""), (2024, "2024")],
)
def test_normalize(crudo: object, esperado: str) -> None:
    assert normalize(crudo) == esperado
