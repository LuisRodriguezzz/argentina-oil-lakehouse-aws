-- Lo que tiene que cumplir una SCD tipo 2: para un mismo pozo, dos tramos no pueden estar
-- vigentes al mismo tiempo. Si se solaparan, el join por rango de las fact devolvería dos
-- filas por mes y duplicaría la producción.
--
-- Un tramo abierto (`vigente_hasta` nulo) se compara como si terminara en el año 9999.

with tramos as (
    select
        idpozo,
        vigente_desde,
        coalesce(vigente_hasta, date '9999-12-31') as vigente_hasta
    from {{ ref('dim_pozo') }}
)

select
    a.idpozo,
    a.vigente_desde as desde_a,
    b.vigente_desde as desde_b
from tramos a
join tramos b
    on a.idpozo = b.idpozo
    and a.vigente_desde < b.vigente_desde
    and b.vigente_desde <= a.vigente_hasta
