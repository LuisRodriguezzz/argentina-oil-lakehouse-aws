-- La cohorte se define como el año de la primera producción. El test fija esa definición: si
-- alguien la cambiara (por ejemplo, al año de la fractura) sin tocar la descripción, la
-- documentación quedaría mintiendo. Devuelve las filas donde no coinciden.
select idpozo, primera_produccion, anio_primera_produccion
from {{ ref('mart_pozo_completacion_produccion') }}
where anio_primera_produccion <> year(primera_produccion)
