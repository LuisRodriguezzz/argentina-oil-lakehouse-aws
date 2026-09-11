-- Un yacimiento por fila, con su última descripción conocida. El nombre del área, la cuenca y
-- la concesión se redeclaran en cada DDJJ y a veces cambian (se renombra un área, se transfiere
-- una concesión): se conserva la última observación, que es la vigente.
--
-- Se resuelve con `max_by` y no con una ventana `row_number`: agrupar por yacimiento es un
-- agregado sobre ~1.000 claves, mucho más barato que ordenar 18,2 M filas.

with observaciones as (
    select
        idareayacimiento,
        anio * 100 + mes as periodo,
        areayacimiento,
        cuenca,
        provincia,
        idareapermisoconcesion,
        areapermisoconcesion
    from {{ source('silver', 'produccion_pozo') }}
    where idareayacimiento is not null
)

select
    {{ md5('idareayacimiento') }} as yacimiento_key,
    idareayacimiento,
    max_by(areayacimiento, periodo) as areayacimiento,
    max_by(cuenca, periodo) as cuenca,
    max_by(provincia, periodo) as provincia,
    max_by(idareapermisoconcesion, periodo) as idareapermisoconcesion,
    max_by(areapermisoconcesion, periodo) as areapermisoconcesion
from observaciones
group by idareayacimiento
