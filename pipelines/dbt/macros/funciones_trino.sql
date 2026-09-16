{#
  Las funciones de Trino que los modelos usan a través de un nombre corto, en un solo lugar.
  Athena es Trino: casi todo el SQL de los modelos es el estándar, y la media docena de
  funciones que se escriben de forma larga o poco evidente se nombran acá para que el modelo
  se lea por lo que hace y no por cómo se deletrea (ADR 0003).

  Solo funciones: un `cast` o un literal se escriben en línea en el modelo, donde se leen de
  una y con el comentario del porqué al lado.

  `fin_de_mes`, `dias_entre` y `meses_entre` no se llaman como la función de Trino porque
  dbt-core ya publica macros `last_day` y `datediff` propias, y redefinirlas en el proyecto
  se las cambiaría también a dbt.
#}


{% macro md5(expresion) -%}
    {# En Trino md5 toma y devuelve varbinary. El cast es porque no toda clave es texto:
       `idareayacimiento` es un código numérico. #}
    lower(to_hex(md5(to_utf8(cast({{ expresion }} as varchar)))))
{%- endmacro %}


{% macro make_date(anio, mes, dia) -%}
    {# Trino no arma fechas desde tres enteros: se escribe el ISO y se parsea. #}
    from_iso8601_date(format('%04d-%02d-%02d', {{ anio }}, {{ mes }}, {{ dia }}))
{%- endmacro %}


{% macro date_format(fecha, formato) -%}
    {# `date_format` existe en Trino pero con patrones de MySQL ('%Y-%m'). El que usa los
       patrones de Java que escriben los modelos es `format_datetime`, y pide timestamp. #}
    format_datetime(cast({{ fecha }} as timestamp), '{{ formato }}')
{%- endmacro %}


{% macro fin_de_mes(fecha) -%}
    last_day_of_month({{ fecha }})
{%- endmacro %}


{% macro dias_entre(desde, hasta) -%}
    date_diff('day', {{ desde }}, {{ hasta }})
{%- endmacro %}


{% macro meses_entre(desde, hasta) -%}
    {# Las dos fechas son un día 1, así que la cuenta de meses es exacta. #}
    date_diff('month', {{ desde }}, {{ hasta }})
{%- endmacro %}


{% macro serie_de_meses(desde, hasta, alias) -%}
    {# `unnest` no va en el SELECT sino en el FROM, y el intervalo se escribe con la cantidad
       entre comillas. #}
    select {{ alias }} from unnest(sequence({{ desde }}, {{ hasta }}, interval '1' month)) as t({{ alias }})
{%- endmacro %}


{% macro mediana(expresion) -%}
    {# Trino no tiene `median`. `approx_percentile` es aproximada (T-digest); para comparar
       curvas tipo el error es despreciable frente a la dispersión entre pozos. #}
    approx_percentile({{ expresion }}, 0.5)
{%- endmacro %}

