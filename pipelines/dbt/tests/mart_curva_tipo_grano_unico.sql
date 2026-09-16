-- El grano prometido es cohorte-formación-subtipo-tipo de pozo-mes. El test `unique` de dbt
-- trabaja sobre una sola columna, así que la unicidad de la clave compuesta va acá: devuelve
-- las combinaciones repetidas, que tienen que ser cero.

select
    anio_primera_produccion,
    formacion,
    sub_tipo_recurso,
    tipopozo,
    meses_desde_primera_produccion,
    count(*) as filas
from {{ ref('mart_curva_tipo') }}
group by anio_primera_produccion, formacion, sub_tipo_recurso, tipopozo, meses_desde_primera_produccion
having count(*) > 1
