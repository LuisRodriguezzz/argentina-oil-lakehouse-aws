"""Genera docs/hallazgos.html: el informe con los gráficos de gold, listo para publicar.

Consulta gold en Athena con boto3 (credenciales y región del perfil de `~/.aws`) y escribe un
HTML autocontenido: los datos viajan adentro de la página como JSON y los gráficos los dibuja
Vega-Lite, que se carga desde un CDN. No hay servidor ni proceso detrás: se abre el archivo,
o lo sirve GitHub Pages desde `docs/`.

Uso: uv run python scripts/informe_gold.py [--suffix _prod] [--salida docs/hallazgos.html]
Cuesta centavos: cuatro consultas, tres sobre tablas de gold ya agregadas y una sobre la fact.
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
from statistics import median, quantiles
from string import Template

import boto3

# La población de todo el informe es una sola, así los gráficos no se contradicen entre sí:
# pozos de petróleo shale de Vaca Muerta con primera producción entre 2016 y 2025. Antes de
# 2016 los pozos eran pilotos de otra escala (3.500 m3 a 12 meses contra 17.000 en 2016) y
# aplastan los ejes sin contar nada nuevo.
COHORTES = list(range(2016, 2026))
MINIMO_POZOS = 20
PRIMERA_COHORTE_OPERADORAS = 2019
# Una celda del mapa de diseño con menos pozos que esto no se muestra: su mediana es ruido.
MINIMO_POR_CELDA = 10
# Dos etiquetas al final de las líneas de la curva se pisan si sus valores están más cerca
# que esto (unos 17 px con el alto del gráfico).
SEPARACION_ETIQUETAS_M3 = 3_500
# Cortes del mapa de diseño, en metros de rama y toneladas de arena por metro. Fijos y
# redondos para que la grilla se lea; el último tramo es abierto.
CORTES_RAMA_M = [1_500, 2_000, 2_500, 3_000, 3_500]
CORTES_ARENA_TN_M = [2.5, 3.0, 3.5, 4.0, 4.5]

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
    round(m.arena_por_metro_tn, 2) as arena_tn_m,
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

# La curva tipo por operadora se arma igual que mart_curva_tipo pero agrupando por empresa,
# sobre las cohortes recientes. La fila 'Conjunto' es la misma cuenta sin agrupar: es la
# referencia gris que aparece en cada panel.
SQL_CURVA_OPERADORAS = """
with pozos as (
    select idpozo, empresa
    from {gold}.dim_pozo
    where es_vigente
      and lower(formacion) = 'vaca muerta'
      and sub_tipo_recurso = 'SHALE'
      and tipopozo = 'Petrolífero'
      and year(primera_produccion) between {desde_operadoras} and {hasta}
),

acumulados as (
    select
        p.empresa,
        f.meses_desde_primera_produccion as mes,
        sum(f.prod_pet) over (
            partition by f.idpozo
            order by f.meses_desde_primera_produccion
            rows between unbounded preceding and current row
        ) as acum
    from {gold}.fact_produccion_mensual f
    join pozos p on p.idpozo = f.idpozo
    where f.meses_desde_primera_produccion between 0 and 35
)

select empresa, mes, count(*) as pozos, round(approx_percentile(acum, 0.5)) as mediana_m3
from acumulados
group by empresa, mes
union all
select 'Conjunto' as empresa, mes, count(*) as pozos, round(approx_percentile(acum, 0.5))
from acumulados
group by mes
order by empresa, mes
"""

# Paleta: un solo azul para las series únicas y su rampa, de claro a oscuro, para la cohorte,
# que es una variable ordenada (más oscuro = más nueva). El resto es tinta y grises; el verde
# y el rojo solo aparecen en las variaciones porcentuales.
AZUL = "#1c5cab"
RAMPA_AZUL = [
    "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
GRIS_REFERENCIA = "#bdbbb5"
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
# color de la serie, sin leyendas salvo donde hay dos series (la tabla de cohortes hace de
# leyenda de la rampa).
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
    "legend": {
        "labelColor": TINTA_SECUNDARIA,
        "titleColor": TINTA_SECUNDARIA,
        "titleFontWeight": "normal",
        "labelFontSize": 12,
    },
    "header": {"labelColor": TINTA, "labelFontSize": 13, "labelFontWeight": 600, "title": None},
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


def recortar_cola(curva: list[dict], serie: str = "cohorte") -> list[dict]:
    """Corta cada serie donde ya no llegó al menos la mitad de sus pozos.

    Una cohorte joven tiene meses altos a los que solo llegaron los pozos que arrancaron en
    enero: la muestra se achica de a decenas y la mediana salta. El mínimo de 20 pozos no
    alcanza contra ese sesgo de selección; la mitad de la serie sí.
    """
    en_mes_cero = {fila[serie]: fila["pozos"] for fila in curva if fila["mes"] == 0}
    return [fila for fila in curva if fila["pozos"] * 2 >= en_mes_cero[fila[serie]]]


def en_mes(curva: list[dict], mes: int, campo: str) -> dict[int, float]:
    """Un valor de la curva por cohorte, en un mes dado: `{cohorte: valor}`."""
    return {fila["cohorte"]: fila[campo] for fila in curva if fila["mes"] == mes}


def variacion(actual: float | None, anterior: float | None) -> float | None:
    """Variación porcentual; nula cuando falta alguno de los dos términos."""
    if actual is None or not anterior:
        return None
    return (actual / anterior - 1) * 100


def por_cohorte(pozos: list[dict]) -> list[dict]:
    """Medianas de diseño y producción por cohorte, exactas, sobre la misma lista de pozos, y
    los deciles del petróleo a 12 meses para ver el piso y el techo de cada generación."""
    grupos: dict[int, list[dict]] = defaultdict(list)
    for pozo in pozos:
        grupos[pozo["cohorte"]].append(pozo)
    filas = []
    for cohorte, grupo in sorted(grupos.items()):
        deciles = quantiles((p["prod_12m"] for p in grupo), n=10, method="inclusive")
        cuartiles = quantiles((p["prod_12m"] for p in grupo), n=4, method="inclusive")
        filas.append(
            {
                "cohorte": cohorte,
                "pozos": len(grupo),
                "mediana_rama_m": median(p["rama_m"] for p in grupo),
                "mediana_etapas": median(p["etapas"] for p in grupo),
                "mediana_prod_12m": cuartiles[1],
                "mediana_m3_por_etapa": median(p["m3_por_etapa"] for p in grupo),
                "mediana_m3_por_metro": median(p["m3_por_metro"] for p in grupo),
                "p10": deciles[0],
                "p25": cuartiles[0],
                "p75": cuartiles[2],
                "p90": deciles[8],
            }
        )
    return filas


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


def tramo(valor: float, cortes: list[float], decimales: int) -> str:
    """Etiqueta del tramo al que cae un valor: "< 1.500", "1.500–2.000", "≥ 3.500"."""
    if valor < cortes[0]:
        return f"< {numero(cortes[0], decimales)}"
    for desde, hasta in zip(cortes, cortes[1:], strict=False):
        if valor < hasta:
            return f"{numero(desde, decimales)}–{numero(hasta, decimales)}"
    return f"≥ {numero(cortes[-1], decimales)}"


def etiquetas_de_tramos(cortes: list[float], decimales: int) -> list[str]:
    """Todos los tramos en orden, para que los ejes del mapa no dependan de qué celdas hay."""
    return [tramo(cortes[0] - 1, cortes, decimales)] + [
        tramo(corte, cortes, decimales) for corte in cortes
    ]


def mapa_de_disenio(pozos: list[dict]) -> list[dict]:
    """Petróleo por metro según rama y arena por metro: una celda por combinación de tramos.

    Es la pregunta de ingeniería del informe: ¿bombear más arena por metro rinde más petróleo
    por metro, y en qué largos de rama? Las celdas con pocos pozos no se muestran.
    """
    celdas: dict[tuple[str, str], list[float]] = defaultdict(list)
    for pozo in pozos:
        if pozo["arena_tn_m"] is None:
            continue
        clave = (
            tramo(pozo["rama_m"], CORTES_RAMA_M, 0),
            tramo(pozo["arena_tn_m"], CORTES_ARENA_TN_M, 1),
        )
        celdas[clave].append(pozo["m3_por_metro"])
    return [
        {"rama": rama, "arena": arena, "pozos": len(valores), "m3_por_metro": median(valores)}
        for (rama, arena), valores in celdas.items()
        if len(valores) >= MINIMO_POR_CELDA
    ]


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


def nombre_corto(empresa: str) -> str:
    """ "VISTA ENERGY ARGENTINA SAU" -> "Vista Energy": las dos primeras palabras que no son
    la forma societaria, en mayúsculas y minúsculas. Para los títulos de los paneles."""
    societarias = {"S.A.", "S.A.U.", "SAU", "S.R.L.", "SRL", "SL", "S.L.", "LTD", "INC"}
    palabras = [p for p in empresa.split() if p.upper() not in societarias]
    return " ".join(p.capitalize() if len(p) > 3 else p for p in palabras[:2])


def curvas_por_operadora(filas: list[dict]) -> list[dict]:
    """Un panel por operadora con muestra suficiente: su curva y, repetida en cada panel, la
    del conjunto como referencia. Las colas se cortan con la misma regla que la curva tipo."""
    filas = recortar_cola(filas, serie="empresa")
    conjunto = [f for f in filas if f["empresa"] == "Conjunto"]
    tamanio = {f["empresa"]: f["pozos"] for f in filas if f["mes"] == 0}
    operadoras = [e for e, n in tamanio.items() if e != "Conjunto" and n >= MINIMO_POZOS]
    paneles = []
    for empresa in operadoras:
        panel = nombre_corto(empresa)
        for f in filas:
            if f["empresa"] == empresa:
                paneles.append({**f, "panel": panel, "serie": "Operadora"})
        for f in conjunto:
            paneles.append({**f, "panel": panel, "serie": "Conjunto"})
    return paneles


def ultimos_puntos(curva: list[dict]) -> list[dict]:
    """El último punto de cada cohorte."""
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


def grafico_distribucion(cohortes: list[dict]) -> dict:
    """Por cohorte, el rango P10–P90 (línea fina), la caja P25–P75 y la mediana del petróleo a
    12 meses: cuánto subió el piso, cuánto el techo y si la dispersión se achicó."""
    x = {"field": "cohorte", "type": "ordinal", "title": "Cohorte", "axis": {"labelAngle": 0}}
    y = {
        "type": "quantitative",
        "title": "Petróleo en los primeros 12 meses (m3)",
        "axis": {"format": ",.0f"},
    }
    tooltip = [
        {"field": "cohorte", "title": "Cohorte"},
        {"field": "pozos", "title": "Pozos"},
        {"field": "p90", "title": "P90", "format": ",.0f"},
        {"field": "p75", "title": "P75", "format": ",.0f"},
        {"field": "mediana_prod_12m", "title": "Mediana", "format": ",.0f"},
        {"field": "p25", "title": "P25", "format": ",.0f"},
        {"field": "p10", "title": "P10", "format": ",.0f"},
    ]
    return {
        "width": "container",
        "height": 340,
        "data": {"values": cohortes},
        "encoding": {"x": x, "tooltip": tooltip},
        "layer": [
            {
                "mark": {"type": "rule", "color": AZUL, "strokeWidth": 1.5},
                "encoding": {"y": {**y, "field": "p10"}, "y2": {"field": "p90"}},
            },
            {
                "mark": {"type": "bar", "width": 14, "color": AZUL, "cornerRadiusEnd": 0},
                "encoding": {"y": {**y, "field": "p25"}, "y2": {"field": "p75"}},
            },
            {
                "mark": {"type": "tick", "color": TARJETA, "thickness": 2, "width": 14},
                "encoding": {"y": {**y, "field": "mediana_prod_12m"}},
            },
        ],
    }


def grafico_trayectoria(cohortes: list[dict]) -> dict:
    """Las diez cohortes unidas en orden en el plano rama × petróleo por metro: la curva de
    aprendizaje del play en un dibujo. El tamaño del punto es la cantidad de pozos."""
    x = {
        "field": "mediana_rama_m",
        "type": "quantitative",
        "title": "Rama horizontal, mediana (m)",
        "scale": {"zero": False, "padding": 24},
        "axis": {"format": ",.0f", "tickCount": 5},
    }
    y = {
        "field": "mediana_m3_por_metro",
        "type": "quantitative",
        "title": "Petróleo por metro a 12 meses, mediana (m3/m)",
        "axis": {"format": ",.0f"},
    }
    return {
        "width": "container",
        "height": 340,
        "data": {"values": cohortes},
        "encoding": {"x": x, "y": y},
        "layer": [
            {
                "mark": {"type": "line", "color": GRIS_REFERENCIA, "strokeWidth": 1.5},
                "encoding": {"order": {"field": "cohorte"}},
            },
            {
                "mark": {"type": "circle", "opacity": 1},
                "encoding": {
                    "color": COLOR_COHORTE,
                    "size": {
                        "field": "pozos",
                        "type": "quantitative",
                        "scale": {"range": [60, 520]},
                        "legend": None,
                    },
                    "tooltip": [
                        {"field": "cohorte", "title": "Cohorte"},
                        {"field": "pozos", "title": "Pozos"},
                        {"field": "mediana_rama_m", "title": "Rama (m)", "format": ",.0f"},
                        {"field": "mediana_etapas", "title": "Etapas", "format": ",.0f"},
                        {
                            "field": "mediana_m3_por_metro",
                            "title": "m3 por metro",
                            "format": ",.1f",
                        },
                        {
                            "field": "mediana_prod_12m",
                            "title": "Petróleo 12 m (m3)",
                            "format": ",.0f",
                        },
                    ],
                },
            },
            {
                "mark": {"type": "text", "dy": -14, "fontSize": 11},
                "encoding": {"text": {"field": "cohorte"}},
            },
        ],
    }


def grafico_mapa_de_disenio(celdas: list[dict]) -> dict:
    """Mapa de calor rama × arena por metro; el color es el petróleo por metro, el número de
    cada celda también, y la cantidad de pozos va en el tooltip."""
    return {
        "width": "container",
        "height": 260,
        "data": {"values": celdas},
        "encoding": {
            "x": {
                "field": "rama",
                "type": "ordinal",
                "title": "Rama horizontal (m)",
                "sort": etiquetas_de_tramos(CORTES_RAMA_M, 0),
                "axis": {"labelAngle": 0, "domain": False, "ticks": False},
            },
            "y": {
                "field": "arena",
                "type": "ordinal",
                "title": "Arena por metro (tn/m)",
                "sort": list(reversed(etiquetas_de_tramos(CORTES_ARENA_TN_M, 1))),
                "axis": {"domain": False, "ticks": False, "grid": False},
            },
            "tooltip": [
                {"field": "rama", "title": "Rama (m)"},
                {"field": "arena", "title": "Arena (tn/m)"},
                {"field": "pozos", "title": "Pozos"},
                {"field": "m3_por_metro", "title": "m3 por metro", "format": ",.1f"},
            ],
        },
        "layer": [
            {
                "mark": {"type": "rect", "stroke": PANEL, "strokeWidth": 2, "cornerRadius": 2},
                "encoding": {
                    "color": {
                        "field": "m3_por_metro",
                        "type": "quantitative",
                        "title": "m3 por metro",
                        "scale": {"range": [RAMPA_AZUL[0], RAMPA_AZUL[-1]]},
                        "legend": {"orient": "right", "gradientLength": 160, "format": ",.0f"},
                    }
                },
            },
            {
                "mark": {"type": "text", "fontSize": 12, "fontWeight": 500},
                "encoding": {
                    "text": {"field": "m3_por_metro", "format": ",.1f"},
                    # Blanco sobre las celdas oscuras, tinta sobre las claras.
                    "color": {
                        "condition": {"test": "datum.m3_por_metro >= 15", "value": TARJETA},
                        "value": TINTA,
                    },
                },
            },
        ],
    }


def grafico_operadoras(paneles: list[dict], orden: list[str]) -> dict:
    """Un panel chico por operadora: su curva en azul contra la del conjunto en gris. La
    referencia repetida es lo que permite ver desde qué mes una operadora se despega."""
    return {
        "data": {"values": paneles},
        "facet": {"field": "panel", "type": "nominal", "sort": orden},
        "columns": 4,
        "spacing": {"row": 28, "column": 22},
        "spec": {
            "width": 196,
            "height": 150,
            "encoding": {
                "x": {
                    "field": "mes",
                    "type": "quantitative",
                    "title": "Mes",
                    "scale": {"domain": [0, 35]},
                    "axis": {"values": [0, 12, 24]},
                },
                "y": {
                    "field": "mediana_m3",
                    "type": "quantitative",
                    "title": "Mediana (m3)",
                    "axis": {"format": ",.0f", "tickCount": 4},
                },
                "color": {
                    "field": "serie",
                    "type": "nominal",
                    "title": None,
                    "scale": {
                        "domain": ["Operadora", "Conjunto"],
                        "range": [AZUL, GRIS_REFERENCIA],
                    },
                    "legend": {"orient": "top", "direction": "horizontal", "symbolType": "stroke"},
                },
                "strokeWidth": {
                    "condition": {"test": "datum.serie == 'Conjunto'", "value": 1.5},
                    "value": 2,
                },
                "tooltip": [
                    {"field": "panel", "title": "Panel"},
                    {"field": "serie", "title": "Serie"},
                    {"field": "mes", "title": "Mes"},
                    {"field": "pozos", "title": "Pozos"},
                    {"field": "mediana_m3", "title": "Mediana (m3)", "format": ",.0f"},
                ],
            },
            "mark": "line",
        },
        "resolve": {"scale": {"y": "shared"}},
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


def pagina(
    corte: str, curva: list[dict], pozos: list[dict], operadoras_curva: list[dict], base: str
) -> str:
    curva = recortar_cola(curva)
    cohortes = por_cohorte(pozos)
    ledger = tabla_de_cohortes(curva, cohortes)
    celdas = mapa_de_disenio(pozos)
    operadoras, conjunto = por_operadora(pozos)
    paneles = curvas_por_operadora(operadoras_curva)
    primera, ultima = cohortes[0], cohortes[-1]
    con_12 = [f for f in ledger if f["acum_12"] is not None]
    con_36 = [f for f in ledger if f["acum_36"] is not None]
    ultima_12, ultima_36 = con_12[-1], con_36[-1]
    desde_2021 = [f["acum_12"] for f in con_12 if f["cohorte"] >= 2021]
    mejor_celda = max(celdas, key=lambda c: c["m3_por_metro"])

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
        f"de {numero(primera['mediana_rama_m'])} a {numero(ultima['mediana_rama_m'])} m "
        f"({delta(variacion(ultima['mediana_rama_m'], primera['mediana_rama_m']))}) y las "
        f"etapas de {numero(primera['mediana_etapas'])} a {numero(ultima['mediana_etapas'])} "
        f"({delta(variacion(ultima['mediana_etapas'], primera['mediana_etapas']))}). El pozo "
        f"del percentil 10, el malo de cada generación, pasó de {numero(primera['p10'])} a "
        f"{numero(ultima['p10'])} m3 ({delta(variacion(ultima['p10'], primera['p10']))}); el "
        f"del percentil 90, de {numero(primera['p90'])} a {numero(ultima['p90'])} "
        f"({delta(variacion(ultima['p90'], primera['p90']))}). El petróleo por metro de rama "
        f"fue de {numero(primera['mediana_m3_por_metro'], 1)} a "
        f"{numero(ultima['mediana_m3_por_metro'], 1)} m3/m "
        f"({delta(variacion(ultima['mediana_m3_por_metro'], primera['mediana_m3_por_metro']))})."
    )
    lectura_mapa = (
        f"La celda que más rinde por metro es rama {mejor_celda['rama']} m con "
        f"{mejor_celda['arena']} tn de arena por metro: {numero(mejor_celda['m3_por_metro'], 1)} "
        f"m3/m sobre {mejor_celda['pozos']} pozos. Las celdas con menos de {MINIMO_POR_CELDA} "
        "pozos no se muestran."
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
        "diseño. Los paneles muestran la curva tipo de cada operadora contra la del conjunto: "
        "se ve desde qué mes se despega, y hacia qué lado."
    )

    orden_paneles = [nombre_corto(f["empresa"]) for f in operadoras]
    especificaciones = [
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            **grafico,
            "config": CONFIGURACION,
        }
        for grafico in (
            grafico_curva_tipo(curva),
            grafico_distribucion(cohortes),
            grafico_trayectoria(cohortes),
            grafico_mapa_de_disenio(celdas),
            grafico_operadoras(paneles, orden_paneles),
        )
    ]
    # `</` cerraría el <script> si apareciera en un nombre; se rompe la secuencia por las dudas.
    datos_json = json.dumps(especificaciones, ensure_ascii=False).replace("</", "<\\/")

    return PLANTILLA.substitute(
        descripcion=(
            "Diez cohortes de pozos de petróleo shale de Vaca Muerta: curva tipo, distribución, "
            "diseño de completación y operadoras, desde las declaraciones juradas de la "
            "Secretaría de Energía."
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
        lectura_mapa=lectura_mapa,
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
    parametros = {
        "gold": base,
        "desde": COHORTES[0],
        "hasta": COHORTES[-1],
        "minimo": MINIMO_POZOS,
        "desde_operadoras": PRIMERA_COHORTE_OPERADORAS,
    }

    ultimo_mes = consultar(athena, SQL_CORTE.format(**parametros), workgroup, base)[0]["ultimo_mes"]
    corte = f"{ultimo_mes // 100}-{ultimo_mes % 100:02d}"
    curva = consultar(athena, SQL_CURVA_TIPO.format(**parametros), workgroup, base)
    pozos = consultar(athena, SQL_POZOS.format(**parametros), workgroup, base)
    operadoras = consultar(athena, SQL_CURVA_OPERADORAS.format(**parametros), workgroup, base)
    print(
        f"{base}: declaraciones hasta {corte}, {len(curva)} puntos de curva, {len(pozos)} pozos, "
        f"{len(operadoras)} puntos de curva por operadora"
    )

    args.salida.write_text(pagina(corte, curva, pozos, operadoras, base), encoding="utf-8")
    print(f"escrito {args.salida} ({args.salida.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
