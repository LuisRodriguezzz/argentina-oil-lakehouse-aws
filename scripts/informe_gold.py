"""Genera docs/hallazgos.html: el informe con los gráficos de gold, listo para publicar.

Consulta gold en Athena con boto3 (credenciales y región del perfil de `~/.aws`) y escribe un
HTML autocontenido: los datos viajan adentro de la página como JSON y los gráficos los dibuja
Vega-Lite, que se carga desde un CDN. No hay servidor ni proceso detrás: se abre el archivo,
o lo sirve GitHub Pages desde `docs/`.

Uso: uv run python scripts/informe_gold.py [--suffix _prod] [--salida docs/hallazgos.html]
Cuesta centavos: tres consultas sobre tablas de gold ya agregadas.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from string import Template

import boto3

# La población de todo el informe es una sola, así los gráficos no se contradicen entre sí:
# pozos de petróleo shale de Vaca Muerta con primera producción entre 2016 y 2025. Antes de
# 2016 los pozos eran pilotos de otra escala (3.500 m3 a 12 meses contra 17.000 en 2016) y
# aplastan los ejes sin contar nada nuevo.
COHORTES = list(range(2016, 2026))
MINIMO_POZOS = 20
PRIMERA_COHORTE_OPERADORAS = 2019
# Por encima de esto un pozo queda fuera del dibujo del scatter (no de las medianas): dos o
# tres declaraciones de 120.000-180.000 m3 aplastan a los otros 1.400 contra el eje.
TOPE_SCATTER_M3 = 100_000
# Dos etiquetas al final de las líneas de la curva se pisan si sus valores están más cerca
# que esto (unos 17 px con el alto del gráfico).
SEPARACION_ETIQUETAS_M3 = 3_500

SQL_CORTE = "select max(anio * 100 + mes) as ultimo_mes from {gold}.fact_produccion_mensual"

SQL_CURVA_TIPO = """
select
    anio_primera_produccion as cohorte,
    meses_desde_primera_produccion as mes,
    pozos_con_declaracion as pozos,
    round(prod_pet_acum_mediana) as mediana_m3
from {gold}.mart_curva_tipo
where lower(formacion) = 'vaca muerta'
  and sub_tipo_recurso = 'SHALE'
  and tipopozo = 'Petrolífero'
  and anio_primera_produccion between {desde} and {hasta}
  and pozos_con_declaracion >= {minimo}
order by cohorte, mes
"""

# El mart de pozos no tiene `tipopozo`: se cruza con el tramo vigente de dim_pozo para quedarse
# con los petrolíferos, igual que hace mart_curva_tipo.
SQL_POZOS = """
select
    m.sigla,
    m.empresa,
    m.anio_primera_produccion as cohorte,
    m.longitud_rama_horizontal_m as rama_m,
    m.cantidad_fracturas as etapas,
    round(m.prod_pet_12m) as prod_12m,
    round(m.prod_pet_12m_por_metro, 1) as m3_por_metro,
    round(m.prod_pet_12m_por_etapa) as m3_por_etapa
from {gold}.mart_pozo_completacion_produccion m
join {gold}.dim_pozo d on d.idpozo = m.idpozo and d.es_vigente
where lower(m.formacion) = 'vaca muerta'
  and m.sub_tipo_recurso = 'SHALE'
  and d.tipopozo = 'Petrolífero'
  and m.anio_primera_produccion between {desde} and {hasta}
  and m.meses_con_declaracion = 12
  and m.prod_pet_12m is not null
  and m.longitud_rama_horizontal_m > 0
  and m.cantidad_fracturas is not null
"""

# Paleta: un solo azul para las series únicas y su rampa, de claro a oscuro, para la cohorte,
# que es una variable ordenada (más oscuro = más nueva). El resto es tinta y grises; el verde
# y el rojo solo aparecen en las variaciones porcentuales.
AZUL = "#1c5cab"
RAMPA_AZUL = [
    "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
TINTA = "#141412"
TINTA_SECUNDARIA = "#4f4e4a"
TINTA_TENUE = "#8a8883"
GRILLA = "#e6e5e0"
EJE = "#c9c8c2"
PLANO = "#fbfbfa"
PANEL = "#f1f1ee"
TARJETA = "#ffffff"
SUBE = "#0f6e33"
BAJA = "#b42318"
FUENTE = '"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif'

# Configuración común de Vega-Lite: marcas finas, grilla recesiva, texto en tinta y no en el
# color de la serie, sin leyendas (la tabla de cohortes hace de leyenda).
CONFIGURACION = {
    "background": "transparent",
    "font": FUENTE,
    "view": {"stroke": None},
    "axis": {
        "gridColor": GRILLA,
        "domainColor": EJE,
        "tickColor": EJE,
        "labelColor": TINTA_TENUE,
        "titleColor": TINTA_SECUNDARIA,
        "titleFontWeight": "normal",
        "labelFontSize": 12,
        "titleFontSize": 12,
        "titlePadding": 10,
    },
    "axisX": {"grid": False},
    "line": {"strokeWidth": 2, "strokeCap": "round", "strokeJoin": "round"},
    "bar": {"cornerRadiusEnd": 4},
    "text": {"color": TINTA_SECUNDARIA, "fontSize": 11},
}
COLOR_COHORTE = {
    "field": "cohorte",
    "type": "ordinal",
    "scale": {"domain": COHORTES, "range": RAMPA_AZUL},
    "legend": None,
}


def consultar(athena, sql: str, workgroup: str, database: str) -> list[dict]:
    """Corre una consulta en Athena y devuelve las filas como diccionarios ya tipados.

    Athena devuelve todo como texto; el tipo de cada columna viene aparte en la metadata y
    acá se usa para convertir enteros y decimales. La ubicación de los resultados la impone
    el workgroup, por eso no se pasa.
    """
    inicio = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup, QueryExecutionContext={"Database": database}
    )
    id_consulta = inicio["QueryExecutionId"]
    while True:
        ejecucion = athena.get_query_execution(QueryExecutionId=id_consulta)["QueryExecution"]
        estado = ejecucion["Status"]
        if estado["State"] == "SUCCEEDED":
            break
        if estado["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"consulta {estado['State']}: {estado.get('StateChangeReason')}")
        time.sleep(1)

    filas: list[dict] = []
    columnas: list[tuple[str, str]] = []
    paginas = athena.get_paginator("get_query_results").paginate(QueryExecutionId=id_consulta)
    for pagina in paginas:
        if not columnas:
            columnas = [
                (c["Name"], c["Type"])
                for c in pagina["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
            ]
        for fila in pagina["ResultSet"]["Rows"]:
            valores = [celda.get("VarCharValue") for celda in fila["Data"]]
            # La primera fila de la primera página es el encabezado: trae los nombres.
            if valores == [nombre for nombre, _ in columnas]:
                continue
            pares = zip(columnas, valores, strict=True)
            filas.append({nombre: convertir(valor, tipo) for (nombre, tipo), valor in pares})
    return filas


def convertir(valor: str | None, tipo: str) -> int | float | str | None:
    if valor is None:
        return None
    if tipo in ("integer", "bigint", "smallint", "tinyint"):
        return int(valor)
    if tipo in ("double", "float", "real", "decimal"):
        return float(valor)
    return valor


# --- Cálculos sobre los datos -------------------------------------------------------------


def recortar_cola(curva: list[dict]) -> list[dict]:
    """Corta cada cohorte donde ya no llegó al menos la mitad de sus pozos.

    Una cohorte joven tiene meses altos a los que solo llegaron los pozos que arrancaron en
    enero: la muestra se achica de a decenas y la mediana salta. El mínimo de 20 pozos no
    alcanza contra ese sesgo de selección; la mitad de la cohorte sí.
    """
    en_mes_cero = {fila["cohorte"]: fila["pozos"] for fila in curva if fila["mes"] == 0}
    return [fila for fila in curva if fila["pozos"] * 2 >= en_mes_cero[fila["cohorte"]]]


def en_mes(curva: list[dict], mes: int, campo: str) -> dict[int, float]:
    """Un valor de la curva por cohorte, en un mes dado: `{cohorte: valor}`."""
    return {fila["cohorte"]: fila[campo] for fila in curva if fila["mes"] == mes}


def variacion(actual: float | None, anterior: float | None) -> float | None:
    """Variación porcentual; nula cuando falta alguno de los dos términos."""
    if actual is None or not anterior:
        return None
    return (actual / anterior - 1) * 100


def por_cohorte(pozos: list[dict]) -> list[dict]:
    """Medianas de diseño y producción por cohorte, exactas, sobre la misma lista de pozos."""
    grupos: dict[int, list[dict]] = defaultdict(list)
    for pozo in pozos:
        grupos[pozo["cohorte"]].append(pozo)
    return [
        {
            "cohorte": cohorte,
            "pozos": len(grupo),
            "mediana_rama_m": median(p["rama_m"] for p in grupo),
            "mediana_etapas": median(p["etapas"] for p in grupo),
            "mediana_prod_12m": median(p["prod_12m"] for p in grupo),
            "mediana_m3_por_etapa": median(p["m3_por_etapa"] for p in grupo),
            "mediana_m3_por_metro": median(p["m3_por_metro"] for p in grupo),
        }
        for cohorte, grupo in sorted(grupos.items())
    ]


def tabla_de_cohortes(curva: list[dict], cohortes: list[dict]) -> list[dict]:
    """Una fila por cohorte con acumulados, diseño y la variación contra la cohorte anterior.

    Los acumulados y el tamaño salen de la curva (toda la cohorte con producción); el diseño,
    de los pozos con fractura declarada. Son dos poblaciones y la nota al pie lo dice.
    """
    tamanio = en_mes(curva, 0, "pozos")
    a_12 = en_mes(curva, 11, "mediana_m3")
    a_36 = en_mes(curva, 35, "mediana_m3")
    disenio = {fila["cohorte"]: fila for fila in cohortes}
    filas: list[dict] = []
    anterior: dict = {}
    for cohorte in COHORTES:
        d = disenio.get(cohorte, {})
        fila = {
            "cohorte": cohorte,
            "pozos": tamanio.get(cohorte),
            "rama_m": d.get("mediana_rama_m"),
            "etapas": d.get("mediana_etapas"),
            "acum_12": a_12.get(cohorte),
            "acum_36": a_36.get(cohorte),
            "m3_por_etapa": d.get("mediana_m3_por_etapa"),
        }
        fila["var_12"] = variacion(fila["acum_12"], anterior.get("acum_12"))
        fila["var_36"] = variacion(fila["acum_36"], anterior.get("acum_36"))
        fila["var_etapa"] = variacion(fila["m3_por_etapa"], anterior.get("m3_por_etapa"))
        filas.append(fila)
        anterior = fila
    return filas


def por_operadora(pozos: list[dict]) -> tuple[list[dict], float]:
    """Productividad por metro por operadora en las cohortes recientes, y la del conjunto.

    Por metro de rama y no por pozo: así una operadora con ramas más largas no gana solo por
    perforar más largo. Desde 2019 para comparar diseños de la misma época. La mediana del
    conjunto es la de todos los pozos de esas cohortes, de todas las operadoras.
    """
    recientes = [pozo for pozo in pozos if pozo["cohorte"] >= PRIMERA_COHORTE_OPERADORAS]
    conjunto = median(pozo["m3_por_metro"] for pozo in recientes)
    grupos: dict[str, list[dict]] = defaultdict(list)
    for pozo in recientes:
        grupos[pozo["empresa"]].append(pozo)
    filas = []
    for empresa, grupo in grupos.items():
        if len(grupo) < MINIMO_POZOS:
            continue
        m3_por_metro = median(p["m3_por_metro"] for p in grupo)
        filas.append(
            {
                "empresa": empresa,
                "pozos": len(grupo),
                "m3_por_metro": m3_por_metro,
                "vs_conjunto": variacion(m3_por_metro, conjunto),
                "prod_12m": median(p["prod_12m"] for p in grupo),
                "rama_m": median(p["rama_m"] for p in grupo),
            }
        )
    return sorted(filas, key=lambda fila: -fila["m3_por_metro"]), conjunto


def ultimos_puntos(curva: list[dict]) -> list[dict]:
    """El último punto de cada cohorte, con el año como texto para la etiqueta."""
    ultimos: dict[int, dict] = {}
    for fila in curva:
        ultimos[fila["cohorte"]] = fila
    return sorted(ultimos.values(), key=lambda f: (-f["mes"], -f["mediana_m3"]))


def etiquetas_al_borde(curva: list[dict]) -> list[dict]:
    """El año de cada cohorte a la derecha de su línea, solo para las que llegan al borde
    derecho, donde no hay otras líneas encima; y salteando las que se pisarían entre sí. Las
    que terminan antes llevan un punto final y el texto dice en qué mes; la tabla de abajo
    trae todas."""
    puestas: list[dict] = []
    for fila in ultimos_puntos(curva):
        if fila["mes"] != 35:
            continue
        if all(
            abs(p["mediana_m3"] - fila["mediana_m3"]) >= SEPARACION_ETIQUETAS_M3 for p in puestas
        ):
            puestas.append(fila)
    return puestas


def terminan_antes(curva: list[dict]) -> list[dict]:
    """Las cohortes cuya línea no llega al mes 35 todavía, con su último mes."""
    return [fila for fila in ultimos_puntos(curva) if fila["mes"] < 35]


# --- Gráficos (especificaciones de Vega-Lite) -----------------------------------------------


def grafico_curva_tipo(curva: list[dict]) -> dict:
    """Una línea por cohorte con su año al final; al pasar el puntero, una regla vertical con
    todas las cohortes de ese mes (el pivot arma una columna por cohorte para el tooltip)."""
    seleccion = {
        "name": "mes_elegido",
        "select": {
            "type": "point",
            "fields": ["mes"],
            "nearest": True,
            "on": "pointermove",
            "clear": "pointerout",
        },
    }
    visible_al_pasar = {
        "condition": {"param": "mes_elegido", "empty": False, "value": 1},
        "value": 0,
    }
    y = {
        "field": "mediana_m3",
        "type": "quantitative",
        "title": "Petróleo acumulado por pozo, mediana (m3)",
        "axis": {"format": ",.0f"},
    }
    return {
        "width": "container",
        "height": 400,
        "padding": {"right": 34},
        "data": {"values": curva},
        "encoding": {
            "x": {
                "field": "mes",
                "type": "quantitative",
                "title": "Meses desde la primera producción",
                "scale": {"domain": [0, 35]},
                "axis": {"values": [0, 6, 12, 18, 24, 30]},
            }
        },
        "layer": [
            {"mark": "line", "encoding": {"y": y, "color": COLOR_COHORTE}},
            {
                "data": {"values": etiquetas_al_borde(curva)},
                "mark": {"type": "text", "align": "left", "dx": 7, "fontSize": 12},
                "encoding": {"y": y, "text": {"field": "cohorte"}},
            },
            {
                "data": {"values": terminan_antes(curva)},
                "mark": {"type": "point", "filled": True, "size": 44},
                "encoding": {"y": y, "color": COLOR_COHORTE},
            },
            {
                "mark": {"type": "point", "filled": True, "size": 60},
                "encoding": {"y": y, "color": COLOR_COHORTE, "opacity": visible_al_pasar},
            },
            {
                "transform": [{"pivot": "cohorte", "value": "mediana_m3", "groupby": ["mes"]}],
                "mark": {"type": "rule", "color": EJE},
                "params": [seleccion],
                "encoding": {
                    "opacity": visible_al_pasar,
                    "tooltip": [{"field": "mes", "title": "Mes"}]
                    + [
                        {
                            "field": str(cohorte),
                            "type": "quantitative",
                            "format": ",.0f",
                            "title": f"Cohorte {cohorte}",
                        }
                        for cohorte in COHORTES
                    ],
                },
            },
        ],
    }


def grafico_rama_vs_produccion(pozos: list[dict]) -> dict:
    return {
        "width": "container",
        "height": 340,
        "data": {"values": pozos},
        "transform": [{"filter": f"datum.prod_12m <= {TOPE_SCATTER_M3}"}],
        "mark": {"type": "circle", "size": 40, "opacity": 0.6},
        "encoding": {
            "x": {
                "field": "rama_m",
                "type": "quantitative",
                "title": "Rama horizontal (m)",
                "axis": {"format": ",.0f", "tickCount": 5},
            },
            "y": {
                "field": "prod_12m",
                "type": "quantitative",
                "title": "Petróleo en los primeros 12 meses (m3)",
                "scale": {"domain": [0, TOPE_SCATTER_M3]},
                "axis": {"format": ",.0f"},
            },
            "color": COLOR_COHORTE,
            "tooltip": [
                {"field": "sigla", "title": "Pozo"},
                {"field": "empresa", "title": "Operadora"},
                {"field": "cohorte", "title": "Cohorte"},
                {"field": "rama_m", "title": "Rama (m)", "format": ",.0f"},
                {"field": "etapas", "title": "Etapas"},
                {"field": "prod_12m", "title": "Petróleo 12 m (m3)", "format": ",.0f"},
            ],
        },
    }


def grafico_por_etapa(cohortes: list[dict]) -> dict:
    return {
        "width": "container",
        "height": 340,
        "data": {"values": cohortes},
        "encoding": {
            "x": {
                "field": "cohorte",
                "type": "ordinal",
                "title": "Cohorte",
                "axis": {"labelAngle": 0},
            },
            "y": {
                "field": "mediana_m3_por_etapa",
                "type": "quantitative",
                "title": "Petróleo por etapa a 12 meses, mediana (m3)",
                "axis": {"format": ",.0f"},
            },
        },
        "layer": [
            {
                "mark": {"type": "bar", "width": 22, "color": AZUL},
                "encoding": {
                    "tooltip": [
                        {"field": "cohorte", "title": "Cohorte"},
                        {"field": "pozos", "title": "Pozos"},
                        {
                            "field": "mediana_m3_por_etapa",
                            "title": "m3 por etapa",
                            "format": ",.0f",
                        },
                        {"field": "mediana_etapas", "title": "Etapas, mediana", "format": ",.0f"},
                        {"field": "mediana_rama_m", "title": "Rama (m), mediana", "format": ",.0f"},
                    ]
                },
            },
            {
                "mark": {"type": "text", "dy": -8},
                "encoding": {"text": {"field": "mediana_m3_por_etapa", "format": ",.0f"}},
            },
        ],
    }


# --- HTML ----------------------------------------------------------------------------------


def numero(valor: float | None, decimales: int = 0) -> str:
    """Números a la argentina: punto de miles, coma decimal. Un guion cuando no hay dato."""
    if valor is None:
        return "—"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "@").replace(".", ",").replace("@", ".")


def delta(pct: float | None, sufijo: str = "") -> str:
    """Variación porcentual con flecha; verde si sube, rojo si baja, gris si no hay dato."""
    if pct is None:
        return '<span class="delta neutra">—</span>'
    clase, flecha, signo = ("sube", "▲", "+") if pct >= 0 else ("baja", "▼", "−")
    return f'<span class="delta {clase}">{flecha} {signo}{numero(abs(pct), 1)} %{sufijo}</span>'


def swatch(cohorte: int) -> str:
    color = RAMPA_AZUL[COHORTES.index(cohorte)]
    return f'<i class="swatch" style="background:{color}"></i>{cohorte}'


def cifra(valor: str, etiqueta: str, variacion_html: str = "") -> str:
    return (
        f'<div class="cifra"><span class="valor">{valor}</span>'
        f'<span class="etiqueta">{etiqueta}</span>{variacion_html}</div>'
    )


def tabla(encabezados: list[str], filas: list[list[str]], izquierda: tuple[int, ...] = (0,)) -> str:
    """Las celdas llegan ya como HTML (los nombres de empresa, escapados). Los números van a
    la derecha; `izquierda` dice qué columnas son texto."""

    def celda(etiqueta: str, indice: int, contenido: str) -> str:
        clase = ' class="izquierda"' if indice in izquierda else ""
        return f"<{etiqueta}{clase}>{contenido}</{etiqueta}>"

    cabeza = "".join(celda("th", i, titulo) for i, titulo in enumerate(encabezados))
    cuerpo = "".join(
        "<tr>" + "".join(celda("td", i, c) for i, c in enumerate(fila)) + "</tr>" for fila in filas
    )
    return f"<table><thead><tr>{cabeza}</tr></thead><tbody>{cuerpo}</tbody></table>"


def html_tabla_cohortes(filas: list[dict]) -> str:
    encabezados = [
        "Cohorte", "Pozos", "Rama (m)", "Etapas",
        "12 meses (m3)", "Δ", "36 meses (m3)", "Δ", "m3 por etapa", "Δ",
    ]  # fmt: skip
    cuerpo = [
        [
            swatch(f["cohorte"]),
            numero(f["pozos"]),
            numero(f["rama_m"]),
            numero(f["etapas"]),
            numero(f["acum_12"]),
            delta(f["var_12"]),
            numero(f["acum_36"]),
            delta(f["var_36"]),
            numero(f["m3_por_etapa"]),
            delta(f["var_etapa"]),
        ]
        for f in filas
    ]
    return tabla(encabezados, cuerpo)


def html_tabla_operadoras(filas: list[dict]) -> str:
    encabezados = [
        "", "Operadora", "Pozos", "m3 por metro", "vs conjunto", "Petróleo 12 m (m3)", "Rama (m)",
    ]  # fmt: skip
    tope = max(f["m3_por_metro"] for f in filas)
    cuerpo = [
        [
            f'<span class="orden">{orden}</span>',
            html.escape(f["empresa"]),
            numero(f["pozos"]),
            f'<span class="barra-caja"><i class="barra" '
            f'style="width:{f["m3_por_metro"] / tope * 100:.0f}%"></i></span>'
            f"{numero(f['m3_por_metro'], 1)}",
            delta(f["vs_conjunto"]),
            numero(f["prod_12m"]),
            numero(f["rama_m"]),
        ]
        for orden, f in enumerate(filas, start=1)
    ]
    return tabla(encabezados, cuerpo, izquierda=(0, 1, 3))


def pagina(corte: str, curva: list[dict], pozos: list[dict], base: str) -> str:
    curva = recortar_cola(curva)
    cohortes = por_cohorte(pozos)
    ledger = tabla_de_cohortes(curva, cohortes)
    operadoras, conjunto = por_operadora(pozos)
    primera, ultima = ledger[0], ledger[-1]
    con_12 = [f for f in ledger if f["acum_12"] is not None]
    con_36 = [f for f in ledger if f["acum_36"] is not None]
    ultima_12, ultima_36 = con_12[-1], con_36[-1]
    desde_2021 = [f["acum_12"] for f in con_12 if f["cohorte"] >= 2021]
    fuera_de_escala = sum(1 for pozo in pozos if pozo["prod_12m"] > TOPE_SCATTER_M3)

    cifras = [
        cifra(
            f"{numero(ultima_12['acum_12'])} m3",
            f"a 12 meses, mediana de la cohorte {ultima_12['cohorte']}",
            delta(ultima_12["var_12"], f" vs {ultima_12['cohorte'] - 1}"),
        ),
        cifra(
            f"{numero(ultima_36['acum_36'])} m3",
            f"a 36 meses, mediana de la cohorte {ultima_36['cohorte']}",
            delta(ultima_36["var_36"], f" vs {ultima_36['cohorte'] - 1}"),
        ),
        cifra(
            f"×{numero(ultima_12['acum_12'] / con_12[0]['acum_12'], 1)}",
            f"a 12 meses, de la cohorte {con_12[0]['cohorte']} a la {ultima_12['cohorte']}",
        ),
    ]

    lectura_curva = (
        f"A los 12 meses, la mediana pasó de {numero(con_12[0]['acum_12'])} m3 en la cohorte "
        f"{con_12[0]['cohorte']} a {numero(ultima_12['acum_12'])} en la {ultima_12['cohorte']}. "
        f"Desde 2021 las cohortes se mueven entre {numero(min(desde_2021))} y "
        f"{numero(max(desde_2021))} m3: la mejora entre generaciones se frenó. Cada línea es "
        f"una cohorte, del azul más claro ({COHORTES[0]}) al más oscuro ({COHORTES[-1]}), y "
        "llega hasta el mes al que ya llegó al menos la mitad de sus pozos: "
        + " y ".join(
            f"la {f['cohorte']} termina en el mes {f['mes']}" for f in terminan_antes(curva)
        )
        + "."
    )
    lectura_diseno = (
        f"Entre las cohortes {primera['cohorte']} y {ultima['cohorte']}, la rama mediana pasó "
        f"de {numero(primera['rama_m'])} a {numero(ultima['rama_m'])} m "
        f"({delta(variacion(ultima['rama_m'], primera['rama_m']))}) y las etapas de "
        f"{numero(primera['etapas'])} a {numero(ultima['etapas'])} "
        f"({delta(variacion(ultima['etapas'], primera['etapas']))}). El petróleo por etapa fue "
        f"de {numero(primera['m3_por_etapa'])} a {numero(ultima['m3_por_etapa'])} m3 "
        f"({delta(variacion(ultima['m3_por_etapa'], primera['m3_por_etapa']))}): los pozos "
        "producen más porque son más grandes, no porque cada etapa rinda más."
    )
    pie_scatter = (
        "Más rama, más petróleo, con mucha dispersión: a igual largo hay pozos que producen el "
        f"doble que otros. {fuera_de_escala} pozos por encima de {numero(TOPE_SCATTER_M3)} m3 "
        "quedan fuera del dibujo, no de las medianas."
    )
    mejor, peor = operadoras[0], operadoras[-1]
    titulo_operadoras = (
        f"Por metro perforado, {numero(mejor['m3_por_metro'] / peor['m3_por_metro'], 1)} veces "
        "de diferencia entre la primera y la última"
    )
    lectura_operadoras = (
        f"La mediana del conjunto es {numero(conjunto, 1)} m3 de petróleo por metro de rama en "
        "los primeros 12 meses. Es una comparación de resultados, no de calidad técnica: cada "
        "operadora trabaja en bloques distintos de la formación, y el bloque pesa tanto como el "
        "diseño."
    )

    especificaciones = [
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            **grafico,
            "config": CONFIGURACION,
        }
        for grafico in (
            grafico_curva_tipo(curva),
            grafico_rama_vs_produccion(pozos),
            grafico_por_etapa(cohortes),
        )
    ]
    # `</` cerraría el <script> si apareciera en un nombre; se rompe la secuencia por las dudas.
    datos_json = json.dumps(especificaciones, ensure_ascii=False).replace("</", "<\\/")

    return PLANTILLA.substitute(
        descripcion=(
            "Diez cohortes de pozos de petróleo shale de Vaca Muerta: curva tipo, diseño de "
            "completación y operadoras, desde las declaraciones juradas de la Secretaría de "
            "Energía."
        ),
        plano=PLANO,
        panel=PANEL,
        tarjeta=TARJETA,
        tinta=TINTA,
        tinta_2=TINTA_SECUNDARIA,
        tenue=TINTA_TENUE,
        grilla=GRILLA,
        azul=AZUL,
        sube=SUBE,
        baja=BAJA,
        fuente=FUENTE,
        primera_cohorte=COHORTES[0],
        ultima_cohorte=COHORTES[-1],
        primera_cohorte_operadoras=PRIMERA_COHORTE_OPERADORAS,
        minimo=MINIMO_POZOS,
        corte=corte,
        fecha=date.today().isoformat(),
        base=base,
        pozos=numero(len(pozos)),
        cifras="".join(cifras),
        lectura_curva=lectura_curva,
        tabla_cohortes=html_tabla_cohortes(ledger),
        lectura_diseno=lectura_diseno,
        pie_scatter=pie_scatter,
        titulo_operadoras=titulo_operadoras,
        lectura_operadoras=lectura_operadoras,
        tabla_operadoras=html_tabla_operadoras(operadoras),
        especificaciones=datos_json,
    )


# La plantilla vive al lado, en informe_gold.html: es HTML y CSS, se lee y se retoca mejor
# en su propio archivo. Los `$nombre` los llena `pagina()`.
PLANTILLA = Template(Path(__file__).with_suffix(".html").read_text(encoding="utf-8"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera el informe de hallazgos desde gold")
    # Prod por defecto y no dev: dev tiene un solo año de producción y las curvas salen vacías.
    parser.add_argument("--suffix", default=os.environ.get("GLUE_DATABASE_SUFFIX", "_prod"))
    parser.add_argument("--salida", default="docs/hallazgos.html", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base = f"gold{args.suffix}"
    workgroup = f"oil-lakehouse-{args.suffix.lstrip('_')}"
    athena = boto3.client("athena")
    parametros = {"gold": base, "desde": COHORTES[0], "hasta": COHORTES[-1], "minimo": MINIMO_POZOS}

    ultimo_mes = consultar(athena, SQL_CORTE.format(**parametros), workgroup, base)[0]["ultimo_mes"]
    corte = f"{ultimo_mes // 100}-{ultimo_mes % 100:02d}"
    curva = consultar(athena, SQL_CURVA_TIPO.format(**parametros), workgroup, base)
    pozos = consultar(athena, SQL_POZOS.format(**parametros), workgroup, base)
    print(f"{base}: declaraciones hasta {corte}, {len(curva)} puntos de curva, {len(pozos)} pozos")

    args.salida.write_text(pagina(corte, curva, pozos, base), encoding="utf-8")
    print(f"escrito {args.salida} ({args.salida.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
