-- Dimensión de pozos con historia (SCD tipo 2): una fila por cada tramo de meses en que los
-- atributos del pozo no cambiaron. Un pozo que pasó de "Extracción Efectiva" a "Abandonado" y
-- volvió deja tres filas, cada una con su ventana de vigencia.
--
-- Se construye con ventanas y no con `dbt snapshot` porque el histórico mensual ya está
-- completo en silver desde 2006: un snapshot serviría para capturar cambios que llegan de a
-- uno, pero acá los 21 años de cambios están todos escritos y se reconstruyen de una vez.
-- La receta es la clásica: `lag` para ver si algo cambió respecto del mes anterior y una suma
-- acumulada de esos cambios para numerar los tramos.

-- La clave del tramo se arma acá arriba y no en el SELECT: son tres macros anidadas y de
-- corrido no se leería qué compone la clave.
{% set clave_del_tramo = "concat_ws('|', " ~ texto('v.idpozo') ~ ', ' ~ texto('v.vigente_desde') ~ ')' %}

with historia as (
    select
        idpozo,
        {{ make_date('anio', 'mes', 1) }} as mes_declarado,
        empresa,
        tipoestado,
        tipopozo,
        tipoextraccion,
        tipo_de_recurso,
        sub_tipo_recurso,
        formacion,
        idareayacimiento,
        sigla,
        profundidad
    from {{ source('silver', 'produccion_pozo') }}
),

-- Los ocho atributos rastreados juntos en un solo texto: comparar una huella es más corto que
-- ocho comparaciones, y el `coalesce` evita que un nulo se compare contra un valor y se pierda
-- el cambio (en SQL `null <> 'X'` no es verdadero).
huella as (
    select
        *,
        concat_ws(
            '|',
            coalesce(empresa, ''),
            coalesce(tipoestado, ''),
            coalesce(tipopozo, ''),
            coalesce(tipoextraccion, ''),
            coalesce(tipo_de_recurso, ''),
            coalesce(sub_tipo_recurso, ''),
            coalesce(formacion, ''),
            coalesce(idareayacimiento, '')
        ) as atributos
    from historia
),

-- Un mes abre tramo si es el primero del pozo o si la huella difiere de la del mes anterior.
-- Un hueco de meses (el pozo dejó de declarar y volvió) no abre tramo: si volvió igual, es el
-- mismo tramo, y lo que la dimensión rastrea son los cambios de atributo, no la continuidad.
cambios as (
    select
        *,
        case
            when lag(atributos) over (partition by idpozo order by mes_declarado) is null then 1
            when lag(atributos) over (partition by idpozo order by mes_declarado) <> atributos then 1
            else 0
        end as abre_tramo
    from huella
),

-- La suma acumulada de las aperturas numera los tramos dentro de cada pozo: 1, 1, 2, 2, 2, 3...
tramos as (
    select
        *,
        sum(abre_tramo) over (
            partition by idpozo
            order by mes_declarado
            rows between unbounded preceding and current row
        ) as tramo
    from cambios
),

-- Un tramo por fila. Los ocho atributos rastreados son constantes dentro del tramo (para eso
-- se cortó), así que `min` devuelve el valor; `sigla` y `profundidad` no se rastrean y se
-- guarda el último valor observado dentro del tramo.
resumen as (
    select
        idpozo,
        tramo,
        min(mes_declarado) as vigente_desde,
        max(mes_declarado) as ultimo_mes,
        min(empresa) as empresa,
        min(tipoestado) as tipoestado,
        min(tipopozo) as tipopozo,
        min(tipoextraccion) as tipoextraccion,
        min(tipo_de_recurso) as tipo_de_recurso,
        min(sub_tipo_recurso) as sub_tipo_recurso,
        min(formacion) as formacion,
        min(idareayacimiento) as idareayacimiento,
        max_by(sigla, mes_declarado) as sigla,
        max_by(profundidad, mes_declarado) as profundidad
    from tramos
    group by idpozo, tramo
),

-- El último tramo de cada pozo es el vigente.
vigencias as (
    select
        *,
        tramo = max(tramo) over (partition by idpozo) as es_vigente
    from resumen
),

padron as (
    select
        idpozo,
        {{ make_date('anio', 'mes', 1) }} as primera_produccion
    from {{ source('silver', 'pozo_primera_produccion') }}
)

select
    {{ md5(clave_del_tramo) }} as pozo_key,
    v.idpozo,
    v.sigla,
    v.empresa,
    {{ clave_empresa('v.empresa') }} as empresa_key,
    v.tipoestado,
    v.tipopozo,
    v.tipoextraccion,
    v.tipo_de_recurso,
    v.sub_tipo_recurso,
    v.formacion,
    v.idareayacimiento,
    {{ md5('v.idareayacimiento') }} as yacimiento_key,
    v.profundidad,
    p.primera_produccion,
    v.vigente_desde,
    -- El tramo vigente no tiene fin: se cierra el día que el pozo declare algo distinto.
    case when v.es_vigente then null else {{ fin_de_mes('v.ultimo_mes') }} end as vigente_hasta,
    v.es_vigente
from vigencias v
left join padron p on v.idpozo = p.idpozo
