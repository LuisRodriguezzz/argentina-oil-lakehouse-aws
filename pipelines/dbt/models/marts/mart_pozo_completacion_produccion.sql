-- Tabla ancha de análisis: un pozo fracturado por fila, con el diseño de su completación de
-- un lado y lo que produjo en sus primeros meses del otro. Es la forma en que se plantea la
-- pregunta del proyecto: cuánto de la productividad temprana se explica por cómo se estimuló.
--
-- Solo entran pozos con al menos una fractura declarada. Los acumulados pueden ser nulos: un
-- pozo fracturado el mes pasado todavía no tiene 12 meses de historia.

with fracturas_ordenadas as (
    select
        idpozo,
        fecha_inicio_fractura,
        tipo_terminacion,
        longitud_rama_horizontal_m,
        cantidad_fracturas,
        arena_bombeada_total_tn,
        agua_inyectada_m3,
        co2_inyectado_m3,
        presion_maxima_psi,
        potencia_equipos_fractura_hp,
        duracion_dias,
        row_number() over (
            partition by idpozo
            order by fecha_inicio_fractura desc, id_base_fractura_adjiv desc
        ) as orden
    from {{ ref('fact_fractura') }}
),

-- Un pozo puede tener varias declaraciones (hasta 6 el mismo día): se toma la última, que es
-- la que describe el pozo tal como quedó terminado.
ultima_fractura as (
    select * from fracturas_ordenadas where orden = 1
),

-- Producción acumulada desde la primera producción. `meses_desde_primera_produccion` vale 0 en
-- el primer mes, así que "a 3 meses" es < 3.
acumulados as (
    select
        idpozo,
        sum(case when meses_desde_primera_produccion < 3 then prod_pet end) as prod_pet_3m,
        sum(case when meses_desde_primera_produccion < 6 then prod_pet end) as prod_pet_6m,
        sum(case when meses_desde_primera_produccion < 12 then prod_pet end) as prod_pet_12m,
        sum(case when meses_desde_primera_produccion < 3 then prod_gas end) as prod_gas_3m,
        sum(case when meses_desde_primera_produccion < 6 then prod_gas end) as prod_gas_6m,
        sum(case when meses_desde_primera_produccion < 12 then prod_gas end) as prod_gas_12m,
        count(*) as meses_con_declaracion
    from {{ ref('fact_produccion_mensual') }}
    where meses_desde_primera_produccion between 0 and 11
    group by idpozo
),

-- Los atributos descriptivos salen del tramo vigente de la dimensión, no de la fractura: es la
-- caracterización actual del pozo.
pozo_vigente as (
    select * from {{ ref('dim_pozo') }} where es_vigente
)

select
    p.pozo_key,
    f.idpozo,
    p.sigla,
    p.empresa,
    p.empresa_key,
    y.cuenca,
    y.provincia,
    y.areayacimiento,
    p.formacion,
    p.tipo_de_recurso,
    p.sub_tipo_recurso,
    p.profundidad,
    p.primera_produccion,

    f.fecha_inicio_fractura,
    f.tipo_terminacion,
    f.longitud_rama_horizontal_m,
    f.cantidad_fracturas,
    f.arena_bombeada_total_tn,
    f.agua_inyectada_m3,
    f.co2_inyectado_m3,
    f.presion_maxima_psi,
    f.potencia_equipos_fractura_hp,
    f.duracion_dias,

    a.prod_pet_3m,
    a.prod_pet_6m,
    a.prod_pet_12m,
    a.prod_gas_3m,
    a.prod_gas_6m,
    a.prod_gas_12m,
    a.meses_con_declaracion,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from ultima_fractura f
left join pozo_vigente p on f.idpozo = p.idpozo
left join {{ ref('dim_yacimiento') }} y on p.idareayacimiento = y.idareayacimiento
left join acumulados a on f.idpozo = a.idpozo
