# Fuente: padrón de pozos de Capítulo IV con fecha de primera producción

Recurso "Padrón de Pozos de Capítulo IV con fecha de primera producción" del mismo dataset
CKAN `produccion-de-petroleo-y-gas-por-pozo` que [`produccion_pozo.md`](produccion_pozo.md).
Es el único recurso agregado del dataset que no viene partido por año: un solo CSV con **una
fila por pozo**, con el año y mes en que ese pozo produjo por primera vez. Sirve para calcular
la edad del pozo y las curvas de declino en gold (según el contrato).

Medido sobre `data/raw/comparacion/padron.csv`, descargado el 2026-09-06: **86.197 filas, 3
columnas**, 1,2 MB.

## Columnas

| Columna | Tipo | Significado |
| --- | --- | --- |
| `idpozo` | bigint | Identificador único del pozo. **Clave primaria** |
| `anio` | int | Año de la primera producción; columna de partición |
| `mes` | int | Mes de la primera producción (1-12) |

## Clave

`idpozo`. Único: 86.197 filas = 86.197 valores distintos, sin duplicados. No hay columnas
nulas en ninguna de las tres.

## Cadencia

El dataset declara `accrualPeriodicity: R/P1M` (mensual) a nivel general; el `last_modified`
de CKAN medido para este recurso puntual es 2026-08-10, en línea con la familia de producción
DDJJ abiertas y cerradas (no con la familia normal, congelada desde marzo).

## Rarezas medidas

- **Distribución por año**: 63.448 pozos (73,6 % del padrón) tienen `anio=2006`, el primer año
  del registro — son los pozos anteriores a 2006 volcados con la fecha de arranque del
  registro, no necesariamente su primera producción real. El resto se reparte de a cientos
  por año hasta 2026 (389 pozos, el año en curso).
- **Cruce con producción 2024** (familia DDJJ abiertas y cerradas): los 82.379 pozos que
  producen en 2024 están **todos** en el padrón — 0 pozos de producción 2024 le faltan al
  padrón. Es la relación esperada: todo pozo que produce en algún momento tiene que tener una
  fecha de primera producción registrada.
- **3.818 pozos del padrón (4,4 %) no aparecen en producción 2024**, y se explican en dos
  grupos:
  - 1.185 (31 %) tienen primera producción en 2025 o 2026: arrancaron después de 2024, así
    que es esperable que no tengan filas en el archivo de 2024.
  - 2.633 (69 %) tienen primera producción en 2023 o antes (1.924 de ellos en 2006) y sin
    embargo no producen en 2024: son pozos que dejaron de producir antes de ese año
    (abandonados, en estudio, etc.), consistente con el tercio de filas de `produccion_pozo`
    que semana 0 ya había identificado como pozos que no producen.
  - Ningún pozo tiene `anio=2024` en el padrón y falta en la producción de 2024: los pozos que
    arrancan un año siempre tienen al menos una fila ese mismo año.

## Decisiones del contrato

- `primary_key: [idpozo]`: no hace falta componer con `anio`/`mes` porque cada pozo aparece
  una sola vez.
- `partition_by: [anio]`, igual criterio que `produccion_pozo`, para que ambas tablas
  particionen por el mismo campo temporal.
- No se agregan columnas propias: el recurso no trae ubicación ni producción, solo la fecha de
  arranque; esos datos se obtienen cruzando por `idpozo` contra `produccion_pozo`.
