-- Calendario mensual del lakehouse: una fila por año-mes desde 2006 (primer año de la serie
-- de producción) hasta diciembre del año en curso. No sale de los datos, se genera: así el
-- calendario no tiene agujeros aunque un mes no traiga declaraciones.
--
-- Llega hasta fin de año y no hasta el mes de hoy porque las fuentes traen fechas futuras: tres
-- fracturas del Adjunto IV declaran inicio en octubre, noviembre y diciembre de 2026 con día y
-- mes invertidos (ids 5489-5491, ver docs/fuentes/fractura.md). Una dimensión de fecha se
-- construye hacia adelante; si se cortara en el mes en curso, esas tres filas de hechos
-- quedarían apuntando a un mes que no existe.

-- El año de corte se resuelve al compilar y no con `current_date()`: Trino lo escribe sin
-- paréntesis y no vale una macro más por un solo uso. La tabla se reconstruye entera cada
-- corrida, así que compilar en enero o en diciembre da lo mismo.
{% set ultimo_mes = modules.datetime.date(run_started_at.year, 12, 1) %}

with meses as (
    {{ serie_de_meses("date '2006-01-01'", "date '" ~ ultimo_mes ~ "'", 'primer_dia') }}
)

select
    year(primer_dia) * 100 + month(primer_dia) as fecha_key,
    year(primer_dia) as anio,
    month(primer_dia) as mes,
    quarter(primer_dia) as trimestre,
    {{ date_format('primer_dia', 'yyyy-MM') }} as anio_mes,
    primer_dia,
    {{ fin_de_mes('primer_dia') }} as ultimo_dia
from meses
