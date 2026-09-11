# Contratos de datos

Un contrato por tabla silver, en YAML versionado. Lo lee una persona para saber qué hay en
la tabla y lo lee `pipelines/spark_jobs/silver_load.py` para castear y validar: no hay dos
definiciones que puedan desincronizarse (ADR 0002).

El job de silver recibe el nombre del contrato como argumento; la máquina de estados le pasa
uno por fuente y se lo puede correr suelto:

```bash
aws glue start-job-run --job-name silver_load_dev --arguments '{"--contract":"produccion_pozo"}'
```

## Formato

| Campo | Qué es |
|---|---|
| `table` | tabla silver que produce el job |
| `source` | tabla bronze de la que lee |
| `primary_key` | columnas que identifican una fila; se deduplica por ellas |
| `partition_by` | columnas de partición Iceberg; el job reemplaza esas particiones |
| `dedupe_by` | opcional: ante claves repetidas gana la fila con el valor más alto |
| `columns` | lista de columnas, en el orden en que quedan en silver |

Cada columna declara `name`, `type` (`int`, `bigint`, `double`, `string`, `boolean`, `date`,
`timestamp`), `nullable`, `description` y, opcionalmente, `min`, `max` o `allowed_values`.

`primary_key` y `partition_by` solo pueden nombrar columnas que existen en bronze: el job no
deriva columnas. Cuando hace falta particionar por año, se usa la columna de año que ya trae
la fuente. El perfil que justifica tipos, `nullable` y rangos de cada contrato (nulos y rangos
medidos sobre bronze) vive en `docs/fuentes/<fuente>.md`.

## Cómo se aplican

- **Casteo**: bronze guarda todo como string. El job recorta (`trim`), convierte `""` en null
  y castea con `try_cast`; `boolean` se arma desde los `t`/`f` que traen los CSV del portal.
- **Checks duros** (`nullable`, columnas ausentes, clave primaria duplicada): frenan el job
  con código 1. La tabla no se toca.
- **Checks blandos** (`min`, `max`, `allowed_values`): la fila no entra a silver, va a
  `<table>_rejects` con el motivo y sus strings originales. Si se rechaza más del 1 % de las
  filas de un recurso, es un problema de fondo y el job también falla.
- Toda corrida queda en `lake.silver.dq_runs`, con filas de entrada, salida y rechazos.

Un cast que falla (texto donde va un número) da null en silencio salvo que la columna sea
`nullable: false`. Si hiciera falta, se agrega como motivo de rechazo en `silver_rules.py`.

## Ninguna columna de `primary_key` puede ser `nullable: true`

El chequeo de unicidad compara las filas contra `count_distinct` de las columnas de la
clave, y `count_distinct` de varias columnas descarta las filas donde alguna es nula (es la
semántica de `COUNT(DISTINCT a, b)` en SQL). Una clave con una columna nullable haría fallar
el check duro por duplicados aunque no haya ninguno. Cuando un valor "no aplica" y forma
parte de la clave, va como valor explícito y no como nulo: en `reservas.yaml`, `certeza` es
`no_aplica` en los recursos contingentes, que el Excel de origen no subdivide.
