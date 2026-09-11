-- El grano prometido es pozo-mes. El test `unique` de dbt trabaja sobre una sola columna, así
-- que la unicidad de una clave compuesta va acá: devuelve las combinaciones repetidas, que en
-- una fact bien cargada son cero.

select
    idpozo,
    fecha_key,
    count(*) as filas
from {{ ref('fact_produccion_mensual') }}
group by idpozo, fecha_key
having count(*) > 1
