-- Curva tipo por cohorte: la producción acumulada que lleva un pozo "típico" a cada mes de
-- vida, entre los pozos que arrancaron el mismo año en la misma formación, subtipo de recurso
-- y tipo de pozo. Es la tabla con la que se comparan generaciones de pozos: la de 2018 contra
-- la de 2022 a los 12 meses, por ejemplo.
--
-- Una fila por cohorte, formación, subtipo, tipo de pozo y mes de vida (0 a 35). Por fila,
-- cuántos pozos declararon ese mes y la media y la mediana de su acumulado hasta ahí. La
-- mediana está porque unos pocos pozos muy buenos tiran la media hacia arriba. El tipo de pozo
-- separa los petrolíferos de los gasíferos: en una misma formación conviven las dos ventanas y
-- mezclarlas hunde la curva de petróleo de las cohortes con más pozos de gas.
--
-- La cohorte 2006 queda afuera: la serie de producción arranca en enero de 2006, así que todo
-- pozo anterior aparece produciendo por primera vez ese mes y su "mes 0" no es un arranque real.

-- Acumulado por pozo con una suma ventana: el orden por mes de vida no tiene empates porque
-- pozo-mes es el grano de la fact.
with acumulados as (
    select
        idpozo,
        meses_desde_primera_produccion,
        sum(prod_pet) over (
            partition by idpozo
            order by meses_desde_primera_produccion
            rows between unbounded preceding and current row
        ) as prod_pet_acum,
        sum(prod_gas) over (
            partition by idpozo
            order by meses_desde_primera_produccion
            rows between unbounded preceding and current row
        ) as prod_gas_acum
    from {{ ref('fact_produccion_mensual') }}
    where meses_desde_primera_produccion between 0 and 35
),

-- Los atributos salen del tramo vigente de la dimensión, igual que en el mart de pozos: es la
-- caracterización actual del pozo, y así las dos tablas se filtran con los mismos valores.
pozo_vigente as (
    select
        idpozo,
        formacion,
        sub_tipo_recurso,
        tipopozo,
        cast(year(primera_produccion) as integer) as anio_primera_produccion
    from {{ ref('dim_pozo') }}
    where es_vigente
)

select
    p.anio_primera_produccion,
    p.formacion,
    p.sub_tipo_recurso,
    p.tipopozo,
    a.meses_desde_primera_produccion,
    count(*) as pozos_con_declaracion,
    avg(a.prod_pet_acum) as prod_pet_acum_media,
    {{ mediana('a.prod_pet_acum') }} as prod_pet_acum_mediana,
    avg(a.prod_gas_acum) as prod_gas_acum_media,
    {{ mediana('a.prod_gas_acum') }} as prod_gas_acum_mediana,
    -- Toda tabla del lakehouse declara su origen: gold se calcula a partir de silver.
    'derived' as data_origin
from acumulados a
join pozo_vigente p on a.idpozo = p.idpozo
where p.anio_primera_produccion > 2006
group by
    p.anio_primera_produccion,
    p.formacion,
    p.sub_tipo_recurso,
    p.tipopozo,
    a.meses_desde_primera_produccion
