-- Unicidad del grano de fact_reservas. Va como test singular y no como `unique` de columna
-- porque la clave es compuesta y el modelo no publica una clave surrogate.
-- El test pasa cuando no devuelve filas.
--
-- Las columnas se repiten en el `group by` en vez de usar `group by all`, que Trino no
-- entiende.

select
    operador,
    cuenca,
    provincia,
    yacimiento,
    hoja,
    tipo_recurso,
    categoria,
    certeza,
    fluido,
    anio_corte,
    count(*) as filas
from {{ ref('fact_reservas') }}
group by
    operador,
    cuenca,
    provincia,
    yacimiento,
    hoja,
    tipo_recurso,
    categoria,
    certeza,
    fluido,
    anio_corte
having count(*) > 1
