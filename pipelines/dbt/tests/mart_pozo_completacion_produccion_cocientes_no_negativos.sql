-- Los tres cocientes salen de columnas que silver ya acota a >= 0 (producción, arena, rama,
-- etapas), así que un valor negativo solo puede ser un error del cálculo. Devuelve las filas
-- que violan la regla: el test pasa si no devuelve ninguna.
select idpozo, prod_pet_12m_por_metro, prod_pet_12m_por_etapa, arena_por_metro_tn
from {{ ref('mart_pozo_completacion_produccion') }}
where prod_pet_12m_por_metro < 0
   or prod_pet_12m_por_etapa < 0
   or arena_por_metro_tn < 0
