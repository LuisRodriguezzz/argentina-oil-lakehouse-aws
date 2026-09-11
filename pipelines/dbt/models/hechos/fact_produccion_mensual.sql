{{ config(partitioned_by=['anio']) }}

-- Hecho central del modelo: una fila por pozo y mes declarado, con las medidas de producción e
-- inyección y las claves hacia las cuatro dimensiones. Particionada por `anio` igual que la
-- tabla silver de la que sale, así una consulta por año lee un solo directorio.
--
-- Se materializa completa y no incremental: silver reescribe particiones enteras cuando un
-- recurso cambia de sha256 (rectificativas de años viejos incluidas), así que un incremental
-- por año nuevo se perdería esas correcciones. Reconstruir los 21 años cuesta unos minutos,
-- que es menos que el riesgo de quedar desincronizado.

with produccion as (
    select
        idpozo,
        anio,
        mes,
        {{ make_date('anio', 'mes', 1) }} as mes_declarado,
        empresa,
        idareayacimiento,
        prod_pet,
        prod_gas,
        prod_agua,
        iny_agua,
        iny_gas,
        iny_co2,
        iny_otro,
        tef
    from {{ source('silver', 'produccion_pozo') }}
),

-- Cada mes se cuelga del tramo de dim_pozo que estaba vigente entonces: es un join por rango
-- sobre `idpozo`, la forma estándar de cargar un hecho contra una dimensión SCD tipo 2.
con_pozo as (
    select
        p.*,
        d.pozo_key
    from produccion p
    left join {{ ref('dim_pozo') }} d
        on p.idpozo = d.idpozo
        and p.mes_declarado >= d.vigente_desde
        and (d.vigente_hasta is null or p.mes_declarado <= d.vigente_hasta)
),

padron as (
    select
        idpozo,
        {{ make_date('anio', 'mes', 1) }} as primera_produccion
    from {{ source('silver', 'pozo_primera_produccion') }}
)

select
    c.pozo_key,
    c.anio * 100 + c.mes as fecha_key,
    {{ clave_empresa('c.empresa') }} as empresa_key,
    {{ md5('c.idareayacimiento') }} as yacimiento_key,
    c.idpozo,
    c.anio,
    c.mes,
    c.prod_pet,
    c.prod_gas,
    c.prod_agua,
    c.iny_agua,
    c.iny_gas,
    c.iny_co2,
    c.iny_otro,
    c.tef,
    -- Edad del pozo en meses: 0 es el mes de su primera producción. Nula si el pozo no figura
    -- en el padrón, que no cubre a todos los que declaran.
    {{ meses_entre('p.primera_produccion', 'c.mes_declarado') }} as meses_desde_primera_produccion,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from con_pozo c
left join padron p on c.idpozo = p.idpozo
