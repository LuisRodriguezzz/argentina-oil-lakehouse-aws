-- Reconciliación entre capas: la producción de petróleo declarada en 2024 tiene que ser la
-- misma en silver y en gold. Si el join por rango de vigencia contra dim_pozo duplicara o
-- perdiera filas, el total se movería y este test lo muestra.
-- El test pasa cuando no devuelve filas.

with gold as (
    select sum(prod_pet) as total from {{ ref('fact_produccion_mensual') }} where anio = 2024
),

silver as (
    select sum(prod_pet) as total
    from {{ source('silver', 'produccion_pozo') }}
    where anio = 2024
)

select
    gold.total as total_gold,
    silver.total as total_silver,
    gold.total - silver.total as diferencia
from gold
cross join silver
-- Tolerancia de 1 m3 sobre millones: los double no se suman en el mismo orden en las dos
-- consultas y el último decimal puede bailar.
where abs(gold.total - silver.total) > 1
