-- Empresas del upstream, unificadas de las tres fuentes que las nombran cada una a su manera:
-- producción dice `empresa`, fractura `empresa_informante` y reservas `operador`. La misma
-- compañía tiene que caer en la misma fila, así que el nombre se normaliza y la clave sale de
-- ahí (macros/claves.sql).

with de_produccion as (
    select empresa as nombre
    from {{ source('silver', 'produccion_pozo') }}
),

de_fractura as (
    select empresa_informante as nombre
    from {{ source('silver', 'fractura') }}
),

de_reservas as (
    select operador as nombre
    from {{ source('silver', 'reservas') }}
),

todas as (
    select nombre from de_produccion
    union all
    select nombre from de_fractura
    union all
    select nombre from de_reservas
)

select distinct
    {{ clave_empresa('nombre') }} as empresa_key,
    {{ nombre_empresa('nombre') }} as empresa,
    -- Marca al grupo YPF (S.A. y sus controladas), la operadora principal del upstream argentino.
    {{ nombre_empresa('nombre') }} like 'YPF%' as es_ypf,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from todas
where nombre is not null
