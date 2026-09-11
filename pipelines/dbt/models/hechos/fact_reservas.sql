-- Reservas y recursos declarados al 31/12 de cada año, con la empresa resuelta contra
-- dim_empresa.
--
-- Silver tiene una fila por concesión y gold agrega las concesiones: un mismo yacimiento puede
-- estar declarado bajo dos permisos y para analizar reservas interesa el yacimiento, no el
-- papel. Son 198.734 filas de silver que quedan en 190.366. Cuenca y provincia sí se conservan
-- en el grano: 98 nombres de yacimiento se repiten en cuencas distintas y sumarlos mezclaría
-- dos yacimientos que solo comparten el nombre.

select
    {{ clave_empresa('operador') }} as empresa_key,
    operador,
    cuenca,
    provincia,
    yacimiento,
    hoja,
    tipo_recurso,
    categoria,
    certeza,
    fluido,
    unidad,
    anio_corte,
    count(distinct concesion) as concesiones,
    -- `sum` ignora nulos: si todas las celdas del grupo venían vacías el total queda nulo, que
    -- es distinto de un cero declarado (docs/fuentes/reservas.md).
    sum(valor) as valor
from {{ source('silver', 'reservas') }}
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
    unidad,
    anio_corte
