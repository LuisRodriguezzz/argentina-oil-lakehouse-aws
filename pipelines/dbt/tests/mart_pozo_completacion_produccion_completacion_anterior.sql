-- La completación elegida para explicar la producción temprana tiene que ser anterior a esa
-- producción siempre que el pozo tenga alguna declaración anterior. Con la regla vieja (la
-- última declaración) fallaban los pozos re-fracturados años después: se les asignaba el
-- diseño nuevo a la producción vieja. Devuelve los pozos que violan la regla.

with anteriores as (
    select distinct f.idpozo
    from {{ ref('fact_fractura') }} f
    join {{ ref('mart_pozo_completacion_produccion') }} m on m.idpozo = f.idpozo
    where m.primera_produccion is null
       or f.fecha_inicio_fractura < m.primera_produccion + interval '1' month
)

select m.idpozo, m.primera_produccion, m.fecha_inicio_fractura
from {{ ref('mart_pozo_completacion_produccion') }} m
join anteriores a on a.idpozo = m.idpozo
where not m.completacion_anterior_a_produccion
