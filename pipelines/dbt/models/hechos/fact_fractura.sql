-- Una fila por operación de fractura declarada en el Adjunto IV: cómo se estimuló el pozo.
-- Es el lado "diseño" del que fact_produccion_mensual cuenta el resultado; se cruzan por
-- `idpozo` o, mejor, por `pozo_key`.

with fractura as (

select
    f.id_base_fractura_adjiv,
    f.idpozo,
    -- Tramo del pozo vigente el día que arrancó el tratamiento: el estado del pozo en el
    -- momento de la fractura, no el de hoy.
    d.pozo_key,
    {{ clave_empresa('f.empresa_informante') }} as empresa_key,
    f.anio * 100 + f.mes as fecha_key,
    f.anio,
    f.sigla,
    f.cuenca,
    f.yacimiento,
    f.formacion_productiva,
    f.tipo_reservorio,
    f.subtipo_reservorio,
    f.tipo_terminacion,
    f.fecha_inicio_fractura,
    f.fecha_fin_fractura,
    -- 41 filas declaran un fin anterior al inicio (docs/fuentes/fractura.md): ahí la duración
    -- queda nula en vez de negativa, que sería un número inventado.
    case
        when f.fecha_fin_fractura >= f.fecha_inicio_fractura
            then {{ dias_entre('f.fecha_inicio_fractura', 'f.fecha_fin_fractura') }}
    end as duracion_dias,
    f.longitud_rama_horizontal_m,
    f.cantidad_fracturas,
    f.arena_bombeada_nacional_tn,
    f.arena_bombeada_importada_tn,
    -- Arena total: una sola de las dos vacías no anula el total, pero si las dos vienen
    -- vacías el total queda nulo, que es distinto de un cero declarado.
    case
        when f.arena_bombeada_nacional_tn is not null or f.arena_bombeada_importada_tn is not null
            then coalesce(f.arena_bombeada_nacional_tn, 0)
                + coalesce(f.arena_bombeada_importada_tn, 0)
    end as arena_bombeada_total_tn,
    f.agua_inyectada_m3,
    f.co2_inyectado_m3,
    f.presion_maxima_psi,
    f.potencia_equipos_fractura_hp,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from {{ source('silver', 'fractura') }} f
left join {{ ref('dim_pozo') }} d
    on f.idpozo = d.idpozo
    and f.fecha_inicio_fractura >= d.vigente_desde
    and (d.vigente_hasta is null or f.fecha_inicio_fractura <= d.vigente_hasta)

)

select
    fractura.*,
    -- Intensidad de arena: toneladas por etapa. Es el número con el que se comparan diseños de
    -- completación entre pozos. nullif por si alguna vez llega un 0 en etapas (el contrato
    -- pide mínimo 1, pero una división por cero no puede depender de eso).
    arena_bombeada_total_tn / nullif(cantidad_fracturas, 0) as arena_por_etapa_tn
from fractura
