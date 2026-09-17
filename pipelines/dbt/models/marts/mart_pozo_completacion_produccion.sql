-- Tabla ancha de análisis: un pozo fracturado por fila, con el diseño de su completación de
-- un lado y lo que produjo en sus primeros meses del otro. Es la forma en que se plantea la
-- pregunta del proyecto: cuánto de la productividad temprana se explica por cómo se estimuló.
--
-- Solo entran pozos con al menos una fractura declarada. Los acumulados pueden ser nulos: un
-- pozo fracturado el mes pasado todavía no tiene 12 meses de historia.
--
-- El Adjunto IV no trae una declaración por pozo. Un mismo trabajo puede venir partido en
-- varias filas (59 pozos, hasta seis el mismo día, algunas repetidas) y 129 pozos se volvieron
-- a fracturar años después. Lo que explica la producción temprana es el trabajo de
-- completación completo que la precedió: acá se consolidan las partes de cada trabajo y se
-- elige el último trabajo anterior a la primera producción.

-- La declaración anterior del mismo pozo, para medir cuánto pasó entre una y otra.
{% set declaracion_anterior = "lag(fecha_inicio_fractura) over (partition by idpozo order by fecha_inicio_fractura)" %}

-- Filas iguales en fechas, rama, etapas y fluidos son la misma declaración cargada dos veces:
-- sumarlas duplicaría el trabajo.
with declaraciones as (
    select distinct
        idpozo,
        fecha_inicio_fractura,
        fecha_fin_fractura,
        tipo_terminacion,
        longitud_rama_horizontal_m,
        cantidad_fracturas,
        arena_bombeada_total_tn,
        agua_inyectada_m3,
        co2_inyectado_m3,
        presion_maxima_psi,
        potencia_equipos_fractura_hp
    from {{ ref('fact_fractura') }}
),

-- Una declaración abre un trabajo nuevo si empieza más de 45 días después de la anterior del
-- mismo pozo. La suma acumulada de aperturas numera los trabajos: la misma receta con la que
-- dim_pozo numera sus tramos.
aperturas as (
    select
        *,
        case
            when {{ declaracion_anterior }} is null then 1
            when {{ dias_entre(declaracion_anterior, 'fecha_inicio_fractura') }} > 45 then 1
            else 0
        end as abre_trabajo
    from declaraciones
),

numeradas as (
    select
        *,
        sum(abre_trabajo) over (
            partition by idpozo
            order by fecha_inicio_fractura
            rows between unbounded preceding and current row
        ) as trabajo
    from aperturas
),

-- Un trabajo por fila: etapas y fluidos se suman; rama, presión y potencia son las máximas;
-- el método de terminación es el de la declaración con más etapas.
trabajos as (
    select
        idpozo,
        trabajo,
        min(fecha_inicio_fractura) as fecha_inicio_fractura,
        max(fecha_fin_fractura) as fecha_fin_fractura,
        max_by(tipo_terminacion, cantidad_fracturas) as tipo_terminacion,
        max(longitud_rama_horizontal_m) as longitud_rama_horizontal_m,
        sum(cantidad_fracturas) as cantidad_fracturas,
        sum(arena_bombeada_total_tn) as arena_bombeada_total_tn,
        sum(agua_inyectada_m3) as agua_inyectada_m3,
        sum(co2_inyectado_m3) as co2_inyectado_m3,
        max(presion_maxima_psi) as presion_maxima_psi,
        max(potencia_equipos_fractura_hp) as potencia_equipos_fractura_hp,
        count(*) as declaraciones
    from numeradas
    group by idpozo, trabajo
),

-- Los atributos descriptivos salen del tramo vigente de la dimensión, no de la fractura: es la
-- caracterización actual del pozo.
pozo_vigente as (
    select * from {{ ref('dim_pozo') }} where es_vigente
),

-- Un trabajo es anterior a la producción si empezó antes de que termine el mes de la primera
-- producción (la fecha del trabajo es un día; la de la producción, un mes). Un pozo que
-- todavía no produjo cuenta como anterior: no hay nada con qué comparar.
con_produccion as (
    select
        t.*,
        p.primera_produccion is null
        or t.fecha_inicio_fractura < p.primera_produccion + interval '1' month
            as anterior_a_produccion
    from trabajos t
    left join pozo_vigente p on t.idpozo = p.idpozo
),

-- El trabajo que explica la producción temprana es el último de los anteriores a ella. Si
-- ninguno lo es (la fractura se declaró con fecha posterior, o es un pozo viejo que se volvió
-- a fracturar), queda el primero y `completacion_anterior_a_produccion` avisa.
candidatas as (
    select
        *,
        row_number() over (
            partition by idpozo
            order by
                anterior_a_produccion desc,
                case when anterior_a_produccion then -trabajo else trabajo end
        ) as orden
    from con_produccion
),

completacion as (
    select * from candidatas where orden = 1
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
)

select
    p.pozo_key,
    c.idpozo,
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
    cast(year(p.primera_produccion) as integer) as anio_primera_produccion,

    c.fecha_inicio_fractura,
    c.tipo_terminacion,
    c.longitud_rama_horizontal_m,
    c.cantidad_fracturas,
    c.arena_bombeada_total_tn,
    c.agua_inyectada_m3,
    c.co2_inyectado_m3,
    c.presion_maxima_psi,
    c.potencia_equipos_fractura_hp,
    -- Del inicio al fin del trabajo entero. Nula si alguna declaración trae el fin antes del
    -- inicio: una duración negativa sería un número inventado.
    case
        when c.fecha_fin_fractura >= c.fecha_inicio_fractura
            then {{ dias_entre('c.fecha_inicio_fractura', 'c.fecha_fin_fractura') }}
    end as duracion_dias,
    c.declaraciones as declaraciones_de_fractura,
    c.anterior_a_produccion as completacion_anterior_a_produccion,

    a.prod_pet_3m,
    a.prod_pet_6m,
    a.prod_pet_12m,
    a.prod_gas_3m,
    a.prod_gas_6m,
    a.prod_gas_12m,
    a.meses_con_declaracion,
    a.prod_pet_12m / nullif(c.longitud_rama_horizontal_m, 0) as prod_pet_12m_por_metro,
    a.prod_pet_12m / nullif(c.cantidad_fracturas, 0) as prod_pet_12m_por_etapa,
    c.arena_bombeada_total_tn / nullif(c.longitud_rama_horizontal_m, 0) as arena_por_metro_tn,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from completacion c
left join pozo_vigente p on c.idpozo = p.idpozo
left join {{ ref('dim_yacimiento') }} y on p.idareayacimiento = y.idareayacimiento
left join acumulados a on c.idpozo = a.idpozo
