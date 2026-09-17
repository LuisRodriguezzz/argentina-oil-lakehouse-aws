"""Genera docs/hallazgos.html: la página con los gráficos de gold, lista para publicar.

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

# La población de los cuatro gráficos es una sola, así nunca se contradicen entre sí: pozos
# de petróleo shale de Vaca Muerta con primera producción entre 2016 y 2025. Antes de 2016 los
# pozos eran pilotos de otra escala (3.500 m3 a 12 meses contra 17.000 en 2016) y aplastan
# los ejes sin contar nada nuevo.
COHORTES = list(range(2016, 2026))
MINIMO_POZOS = 20
PRIMERA_COHORTE_OPERADORAS = 2019
# Por encima de esto un pozo queda fuera del dibujo del scatter (no de las medianas): dos o
# tres declaraciones de 120.000-180.000 m3 aplastan a los otros 1.400 contra el eje.
TOPE_SCATTER_M3 = 100_000

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
# que es una variable ordenada (más oscuro = más nueva). El resto es tinta y grises.
AZUL = "#2a78d6"
RAMPA_AZUL = [
    "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
TINTA = "#0b0b0b"
TINTA_SECUNDARIA = "#52514e"
TINTA_TENUE = "#898781"
GRILLA = "#e1e0d9"
EJE = "#c3c2b7"
SUPERFICIE = "#fcfcfb"
PLANO = "#f9f9f7"
FUENTE = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Configuración común de Vega-Lite: marcas finas, grilla recesiva, texto en tinta y no en el
# color de la serie.
CONFIGURACION = {
    "background": SUPERFICIE,
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
    "line": {"strokeWidth": 2, "strokeCap": "round", "strokeJoin": "round"},
    "bar": {"cornerRadiusEnd": 4},
    "text": {"color": TINTA_SECUNDARIA, "fontSize": 11},
}
ESCALA_COHORTE = {"domain": COHORTES, "range": RAMPA_AZUL}


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
        estado = athena.get_query_execution(QueryExecutionId=id_consulta)["QueryExecution"][
            "Status"
        ]
        if estado["State"] == "SUCCEEDED":
            break
        if estado["State"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"consulta {estado['State']}: {estado.get('StateChangeReason')}")
        time.sleep(1)

    filas: list[dict] = []
    columnas: list[tuple[str, str]] = []
    for pagina in athena.get_paginator("get_query_results").paginate(QueryExecutionId=id_consulta):
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


def por_operadora(pozos: list[dict]) -> list[dict]:
    """Productividad por metro por operadora, en las cohortes recientes y con muestra mínima.

    Por metro de rama y no por pozo: así una operadora con ramas más largas no gana solo por
    perforar más largo. Desde 2019 para comparar diseños de la misma época.
    """
    grupos: dict[str, list[dict]] = defaultdict(list)
    for pozo in pozos:
        if pozo["cohorte"] >= PRIMERA_COHORTE_OPERADORAS:
            grupos[pozo["empresa"]].append(pozo)
    filas = [
        {
            "empresa": empresa,
            "pozos": len(grupo),
            "mediana_m3_por_metro": median(p["m3_por_metro"] for p in grupo),
            "mediana_prod_12m": median(p["prod_12m"] for p in grupo),
        }
        for empresa, grupo in grupos.items()
        if len(grupo) >= MINIMO_POZOS
    ]
    return sorted(filas, key=lambda fila: -fila["mediana_m3_por_metro"])


def recortar_cola(curva: list[dict]) -> list[dict]:
    """Corta cada cohorte donde ya no llegó al menos la mitad de sus pozos.

    Una cohorte joven tiene meses altos a los que solo llegaron los pozos que arrancaron en
    enero: la muestra se achica de a decenas y la mediana salta. El mínimo de 20 pozos no
    alcanza contra ese sesgo de selección; la mitad de la cohorte sí.
    """
    en_mes_cero = {fila["cohorte"]: fila["pozos"] for fila in curva if fila["mes"] == 0}
    return [fila for fila in curva if fila["pozos"] * 2 >= en_mes_cero[fila["cohorte"]]]


def mediana_en_mes(curva: list[dict], mes: int) -> dict | None:
    """La cohorte más nueva que ya llegó a ese mes con muestra suficiente, y su mediana."""
    filas = [fila for fila in curva if fila["mes"] == mes]
    return max(filas, key=lambda fila: fila["cohorte"]) if filas else None


def grafico_curva_tipo(curva: list[dict]) -> dict:
    """Una línea por cohorte; al pasar el puntero, una regla vertical con todas las cohortes
    de ese mes (el pivot arma una columna por cohorte para el tooltip)."""
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
    color = {
        "field": "cohorte",
        "type": "ordinal",
        "title": "Cohorte",
        "scale": ESCALA_COHORTE,
        "legend": {"symbolType": "stroke"},
    }
    y = {
        "field": "mediana_m3",
        "type": "quantitative",
        "title": "Petróleo acumulado por pozo, mediana (m3)",
        "axis": {"format": ",.0f"},
    }
    return {
        "width": "container",
        "height": 380,
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
            {"mark": "line", "encoding": {"y": y, "color": color}},
            {
                "mark": {"type": "point", "filled": True, "size": 60},
                "encoding": {
                    "y": y,
                    "color": color,
                    "opacity": {
                        "condition": {"param": "mes_elegido", "empty": False, "value": 1},
                        "value": 0,
                    },
                },
            },
            {
                "transform": [{"pivot": "cohorte", "value": "mediana_m3", "groupby": ["mes"]}],
                "mark": {"type": "rule", "color": EJE},
                "params": [seleccion],
                "encoding": {
                    "opacity": {
                        "condition": {"param": "mes_elegido", "empty": False, "value": 1},
                        "value": 0,
                    },
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
        "height": 400,
        "data": {"values": pozos},
        "transform": [{"filter": f"datum.prod_12m <= {TOPE_SCATTER_M3}"}],
        "mark": {"type": "circle", "size": 44, "opacity": 0.6},
        "encoding": {
            "x": {
                "field": "rama_m",
                "type": "quantitative",
                "title": "Rama horizontal (m)",
                "axis": {"format": ",.0f"},
            },
            "y": {
                "field": "prod_12m",
                "type": "quantitative",
                "title": "Petróleo en los primeros 12 meses (m3)",
                "scale": {"domain": [0, TOPE_SCATTER_M3]},
                "axis": {"format": ",.0f"},
            },
            "color": {
                "field": "cohorte",
                "type": "ordinal",
                "title": "Cohorte",
                "scale": ESCALA_COHORTE,
            },
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
        "height": 320,
        "data": {"values": cohortes},
        "encoding": {
            "x": {
                "field": "cohorte",
                "type": "ordinal",
                "title": "Cohorte (año de primera producción)",
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
                "mark": {"type": "bar", "width": 24, "color": AZUL},
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
                        {
                            "field": "mediana_prod_12m",
                            "title": "Petróleo 12 m (m3)",
                            "format": ",.0f",
                        },
                    ]
                },
            },
            {
                "mark": {"type": "text", "dy": -8},
                "encoding": {"text": {"field": "mediana_m3_por_etapa", "format": ",.0f"}},
            },
        ],
    }


def grafico_operadoras(operadoras: list[dict]) -> dict:
    return {
        "width": "container",
        "height": {"step": 30},
        "data": {"values": operadoras},
        "encoding": {
            "y": {
                "field": "empresa",
                "type": "nominal",
                "sort": "-x",
                "title": None,
                "axis": {"labelLimit": 280, "labelColor": TINTA_SECUNDARIA},
            },
            "x": {
                "field": "mediana_m3_por_metro",
                "type": "quantitative",
                "title": "Petróleo por metro de rama a 12 meses, mediana (m3/m)",
                "axis": {"format": ",.0f", "tickCount": 6},
            },
        },
        "layer": [
            {
                "mark": {"type": "bar", "height": 18, "color": AZUL},
                "encoding": {
                    "tooltip": [
                        {"field": "empresa", "title": "Operadora"},
                        {"field": "pozos", "title": "Pozos"},
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
                    ]
                },
            },
            {
                "mark": {"type": "text", "align": "left", "dx": 6},
                "encoding": {"text": {"field": "mediana_m3_por_metro", "format": ",.1f"}},
            },
        ],
    }


def formatear(valor: object) -> str:
    """Números a la argentina: punto de miles, coma decimal. Lo demás, como texto."""
    if isinstance(valor, float) and not valor.is_integer():
        return f"{valor:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")
    if isinstance(valor, (int, float)):
        return f"{int(valor):,}".replace(",", ".")
    return html.escape(str(valor))


def tabla(filas: list[dict], columnas: dict[str, str]) -> str:
    """La tabla es la versión accesible de cada gráfico: los mismos números, sin color."""
    encabezado = "".join(f"<th>{titulo}</th>" for titulo in columnas.values())
    cuerpo = "".join(
        "<tr>" + "".join(f"<td>{formatear(fila[campo])}</td>" for campo in columnas) + "</tr>"
        for fila in filas
    )
    return f"<table><thead><tr>{encabezado}</tr></thead><tbody>{cuerpo}</tbody></table>"


def seccion(numero: int, titulo: str, subtitulo: str, lectura: str, datos: str) -> str:
    return f"""
    <figure class="grafico">
      <h2>{titulo}</h2>
      <p class="subtitulo">{subtitulo}</p>
      <div id="grafico-{numero}" class="lienzo"></div>
      <figcaption>{lectura}</figcaption>
      <details><summary>Ver los datos</summary>{datos}</details>
    </figure>"""


def cifra(valor: str, etiqueta: str) -> str:
    return (
        f'<div class="cifra"><span class="valor">{valor}</span>'
        f'<span class="etiqueta">{etiqueta}</span></div>'
    )


# La plantilla vive al lado, en informe_gold.html: es HTML y CSS, se lee y se retoca mejor
# en su propio archivo. Los `$nombre` los llena `pagina()`.
PLANTILLA = Template(Path(__file__).with_suffix(".html").read_text(encoding="utf-8"))


def pagina(corte: str, curva: list[dict], pozos: list[dict], base: str) -> str:
    curva = recortar_cola(curva)
    cohortes = por_cohorte(pozos)
    operadoras = por_operadora(pozos)
    fuera_de_escala = sum(1 for pozo in pozos if pozo["prod_12m"] > TOPE_SCATTER_M3)
    a_12 = mediana_en_mes(curva, 11)
    a_36 = mediana_en_mes(curva, 35)

    cifras = [
        cifra(formatear(len(pozos)), "pozos con fractura y 12 meses de producción"),
        cifra(
            formatear(a_12["mediana_m3"]) + " m3",
            f"petróleo a 12 meses, mediana de la cohorte {a_12['cohorte']}",
        ),
        cifra(
            formatear(a_36["mediana_m3"]) + " m3",
            f"petróleo a 36 meses, mediana de la cohorte {a_36['cohorte']}",
        ),
        cifra(
            formatear(len(operadoras)),
            f"operadoras con {MINIMO_POZOS} pozos o más desde {PRIMERA_COHORTE_OPERADORAS}",
        ),
    ]

    secciones = [
        seccion(
            1,
            "Curva tipo por cohorte",
            "Petróleo acumulado por pozo, mediana de cada cohorte, mes a mes desde la primera "
            "producción.",
            f"Cada línea es una generación de pozos; el color va del más claro ({COHORTES[0]}) al "
            f"más oscuro ({COHORTES[-1]}). Cada línea llega hasta el mes al que ya llegó al menos "
            "la mitad de su cohorte; más allá, la mediana solo hablaría de los pozos que "
            "arrancaron temprano. La distancia entre líneas es la mejora de diseño entre "
            "generaciones; cuando dejan de separarse, el diseño maduró.",
            tabla(
                curva,
                {
                    "cohorte": "Cohorte",
                    "mes": "Mes",
                    "pozos": "Pozos",
                    "mediana_m3": "Mediana (m3)",
                },
            ),
        ),
        seccion(
            2,
            "Rama horizontal y producción, pozo por pozo",
            "Cada punto es un pozo: largo de la rama horizontal contra petróleo en sus primeros "
            "12 meses.",
            "Más rama, más petróleo, pero con mucha dispersión: a igual largo hay pozos que "
            "producen el doble que otros. Los pozos nuevos (más oscuros) están a la derecha y "
            "arriba: ramas más largas y más producción. Por eso el gráfico siguiente normaliza "
            f"por etapa. {fuera_de_escala} pozos por encima de {formatear(TOPE_SCATTER_M3)} m3 "
            "quedan fuera del dibujo, no de las medianas.",
            tabla(
                cohortes,
                {
                    "cohorte": "Cohorte",
                    "pozos": "Pozos",
                    "mediana_rama_m": "Rama (m)",
                    "mediana_etapas": "Etapas",
                    "mediana_prod_12m": "Petróleo 12 m (m3)",
                },
            ),
        ),
        seccion(
            3,
            "Petróleo por etapa de fractura, por cohorte",
            "Mediana de los primeros 12 meses dividida por la cantidad de etapas, en cada cohorte.",
            "Si la producción por pozo sube solo porque se bombean más etapas, la producción por "
            "etapa se queda quieta o baja. Este gráfico separa el tamaño del diseño de su "
            "eficiencia. Al pasar el puntero se ven las medianas de etapas y de rama de cada "
            "cohorte.",
            tabla(
                cohortes,
                {
                    "cohorte": "Cohorte",
                    "pozos": "Pozos",
                    "mediana_m3_por_etapa": "m3 por etapa",
                    "mediana_m3_por_metro": "m3 por metro",
                    "mediana_etapas": "Etapas",
                },
            ),
        ),
        seccion(
            4,
            "Operadoras: petróleo por metro de rama",
            f"Mediana por operadora, cohortes {PRIMERA_COHORTE_OPERADORAS} en adelante, "
            f"solo con {MINIMO_POZOS} pozos o más.",
            "Por metro y no por pozo: así una operadora no gana solo por perforar más largo. Es "
            "una comparación de resultados, no de calidad técnica: cada operadora trabaja en "
            "bloques distintos de la formación, y el bloque pesa tanto como el diseño.",
            tabla(
                operadoras,
                {
                    "empresa": "Operadora",
                    "pozos": "Pozos",
                    "mediana_m3_por_metro": "m3 por metro",
                    "mediana_prod_12m": "Petróleo 12 m (m3)",
                },
            ),
        ),
    ]

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
            grafico_operadoras(operadoras),
        )
    ]
    # `</` cerraría el <script> si apareciera en un nombre; se rompe la secuencia por las dudas.
    datos_json = json.dumps(especificaciones, ensure_ascii=False).replace("</", "<\\/")

    return PLANTILLA.substitute(
        descripcion=(
            "Curvas tipo por cohorte, rama contra producción, petróleo por etapa y ranking de "
            "operadoras en Vaca Muerta, desde las declaraciones juradas de la Secretaría de "
            "Energía."
        ),
        plano=PLANO,
        superficie=SUPERFICIE,
        tinta=TINTA,
        tinta_2=TINTA_SECUNDARIA,
        tenue=TINTA_TENUE,
        grilla=GRILLA,
        azul=AZUL,
        fuente=FUENTE,
        primera_cohorte=COHORTES[0],
        ultima_cohorte=COHORTES[-1],
        corte=corte,
        fecha=date.today().isoformat(),
        base=base,
        cifras="".join(cifras),
        secciones="".join(secciones),
        pozos=formatear(len(pozos)),
        minimo=MINIMO_POZOS,
        primera_cohorte_operadoras=PRIMERA_COHORTE_OPERADORAS,
        especificaciones=datos_json,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera la página de hallazgos desde gold")
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
