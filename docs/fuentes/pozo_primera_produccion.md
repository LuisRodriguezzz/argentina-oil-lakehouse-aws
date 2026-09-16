# Fuente: padrón de pozos de Capítulo IV con fecha de primera producción

Recurso "Padrón de Pozos de Capítulo IV con fecha de primera producción" del mismo dataset
CKAN `produccion-de-petroleo-y-gas-por-pozo` que [`produccion_pozo.md`](produccion_pozo.md).
Es el único recurso agregado del dataset que no viene partido por año: un solo CSV con **una
fila por pozo**, con el año y mes en que ese pozo produjo por primera vez. Iba a ser el origen
de la edad del pozo en gold, pero el mes no es confiable (ver "Rarezas medidas"): gold calcula
la primera producción desde las declaraciones y guarda la del padrón aparte, como cruce
(`dim_pozo.primera_produccion_padron`).

Medido el 2026-09-06 sobre el CSV descargado del portal, en el proyecto de origen (ADR 0006):
**86.197 filas, 3 columnas**, 1,2 MB.

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
- **El mes solo es real en el año en curso** (medido el 2026-09-16 sobre silver en prod):
  85.810 de los 86.197 pozos (99,55 %) tienen `mes=1`. Los 387 restantes están entre febrero y
  noviembre y coinciden con los pozos de 2026; para esos, el padrón es igual al primer mes
  declarado en `produccion_pozo` en el 100 % de los casos. Es decir: el padrón registra el mes
  de la primera DDJJ, y el portal lo pisa con enero cuando el año cierra. El año sí se
  conserva: coincide con el del primer mes con producción mayor a cero en el 74-88 % de los
  pozos según la cohorte; el resto declaró un año y produjo recién al siguiente, o nunca
  produjo (inyectores, secos).
  Consecuencia en gold: `dim_pozo.primera_produccion` se calcula desde `produccion_pozo` como
  el primer mes con `prod_pet > 0 or prod_gas > 0`, y de ahí salen `meses_desde_primera_produccion`,
  los `prod_*_12m` del mart de pozos y la curva tipo. La fecha del padrón queda en
  `dim_pozo.primera_produccion_padron` para cruzar. Hasta el 2026-09-16 gold usaba el padrón, y
  "12 meses" era en realidad "hasta diciembre del año de arranque".

## Decisiones del contrato

- `primary_key: [idpozo]`: no hace falta componer con `anio`/`mes` porque cada pozo aparece
  una sola vez.
- `partition_by: [anio]`, igual criterio que `produccion_pozo`, para que ambas tablas
  particionen por el mismo campo temporal.
- No se agregan columnas propias: el recurso no trae ubicación ni producción, solo la fecha de
  arranque; esos datos se obtienen cruzando por `idpozo` contra `produccion_pozo`.
