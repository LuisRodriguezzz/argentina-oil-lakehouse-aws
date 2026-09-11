-- Calendario mensual del lakehouse: una fila por año-mes desde 2006 (primer año de la serie
-- de producción) hasta diciembre del año que viene. No sale de los datos, se genera: así el
-- calendario no tiene agujeros aunque un mes no traiga declaraciones.
--
-- Llega un año más allá del corriente porque las fuentes traen fechas futuras: tres fracturas
-- del Adjunto IV declaran inicio en octubre, noviembre y diciembre de 2026 con día y mes
-- invertidos (ids 5489-5491, ver docs/fuentes/fractura.md). Una dimensión de fecha se construye
-- hacia adelante; si se cortara en diciembre del año en curso, esas tres filas de hechos
-- quedarían apuntando a un mes que no existe.

-- El año de corte se resuelve al compilar y no con `current_date()`: Trino lo escribe sin
-- paréntesis y no vale una macro más por un solo uso. La tabla se reconstruye entera cada
-- corrida, así que compilar en enero o en diciembre da lo mismo.
{% set ultimo_mes = modules.datetime.date(run_started_at.year + 1, 12, 1) %}

with meses as (
    {{ serie_de_meses("date '2006-01-01'", "date '" ~ ultimo_mes ~ "'", 'primer_dia') }}
)

-- Los cast a `integer` no son decorativos: en Trino `year()`, `month()` y `quarter()`
-- devuelven bigint, y las fact calculan `fecha_key` sobre columnas integer. Sin el cast la
-- dimensión y los hechos guardarían la misma clave en dos tipos distintos.
select
    cast(year(primer_dia) * 100 + month(primer_dia) as integer) as fecha_key,
    cast(year(primer_dia) as integer) as anio,
    cast(month(primer_dia) as integer) as mes,
    cast(quarter(primer_dia) as integer) as trimestre,
    {{ date_format('primer_dia', 'yyyy-MM') }} as anio_mes,
    primer_dia,
    {{ fin_de_mes('primer_dia') }} as ultimo_dia,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from meses
