-- Las medidas son medias y medianas de acumulados de prod_pet y prod_gas, que el contrato de
-- silver ya acota a >= 0, así que un valor negativo solo puede ser un error de la ventana o de
-- la agregación. Devuelve las filas que violan la regla: el test pasa si no devuelve ninguna.
select
    anio_primera_produccion,
    formacion,
    sub_tipo_recurso,
    tipopozo,
    meses_desde_primera_produccion,
    prod_pet_acum_media,
    prod_pet_acum_mediana,
    prod_gas_acum_media,
    prod_gas_acum_mediana
from {{ ref('mart_curva_tipo') }}
where prod_pet_acum_media < 0
   or prod_pet_acum_mediana < 0
   or prod_gas_acum_media < 0
   or prod_gas_acum_mediana < 0
