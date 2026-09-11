{#
  Las dos claves surrogate que se calculan en más de un modelo. Están acá y no repetidas en
  cada SQL porque si dim_empresa y una fact normalizaran el nombre distinto, la fact quedaría
  apuntando a una empresa que no existe. El test `relationships` lo detectaría, pero es más
  barato tener una sola definición.

  `md5` sale de `macros/funciones_trino.sql`.
#}

{% macro nombre_empresa(columna) -%}
    {# La barra del patrón va sola: Trino no interpreta las secuencias de escape dentro de la
       comilla simple, así que '\s+' ya es la expresión regular. #}
    regexp_replace(upper(trim({{ columna }})), '\s+', ' ')
{%- endmacro %}


{% macro clave_empresa(columna) -%}
    {{ md5(nombre_empresa(columna)) }}
{%- endmacro %}
