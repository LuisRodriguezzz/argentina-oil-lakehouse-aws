-- La arena por etapa sale de dos columnas que el contrato de silver ya acota a >= 0 y >= 1,
-- así que un valor negativo solo puede ser un error del cálculo. Devuelve las filas que
-- violan la regla: el test pasa si no devuelve ninguna.
select id_base_fractura_adjiv, arena_por_etapa_tn
from {{ ref('fact_fractura') }}
where arena_por_etapa_tn < 0
