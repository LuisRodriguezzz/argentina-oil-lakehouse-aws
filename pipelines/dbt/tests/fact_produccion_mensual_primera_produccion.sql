-- La definición de primera producción, como regla: en el mes 0 hay petróleo o gas mayor a
-- cero, y antes del mes 0 no lo hay. Con la fecha del padrón (mes pisado a enero en los años
-- cerrados) esto fallaba en 11 de cada 12 pozos; si alguien vuelve a esa fuente, vuelve a
-- fallar. Devuelve las filas que violan la regla: el test pasa si no devuelve ninguna.
select idpozo, fecha_key, meses_desde_primera_produccion, prod_pet, prod_gas
from {{ ref('fact_produccion_mensual') }}
where (meses_desde_primera_produccion = 0 and not (prod_pet > 0 or prod_gas > 0))
   or (meses_desde_primera_produccion < 0 and (prod_pet > 0 or prod_gas > 0))
