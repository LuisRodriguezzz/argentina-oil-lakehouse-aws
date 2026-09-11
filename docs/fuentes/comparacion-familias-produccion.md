# Comparación: familia "normal" vs "DDJJ abiertas y cerradas" (producción de pozos 2024)

Pendiente 5 de [`docs/semana-0-derisking.md`](../semana-0-derisking.md). El dataset CKAN
`produccion-de-petroleo-y-gas-por-pozo` publica cada año en dos recursos con el mismo
contenido aparente pero nombres distintos: "Producción de Pozos de Gas y Petróleo - 2024"
(familia **normal**) y "... - 2024 (DDJJ abiertas y cerradas)" (familia **DDJJ**). Se
descargaron ambos completos por `http://` el 2026-09-06 y se compararon con Polars
(`Float64` forzado en columnas numéricas, `encoding="utf8-lossy"`,
`infer_schema_length=100_000`).

## Metadatos de los recursos (CKAN `package_show`)

| | Normal (`43a09dce…`) | DDJJ abiertas y cerradas (`0a352dee…`) |
| --- | --- | --- |
| Tamaño descargado | 319,9 MB | 307,6 MB |
| `created` (CKAN) | 2024-01-09 | 2024-02-09 |
| `last_modified` (CKAN) | **2026-03-03** | **2026-08-04** |
| Descripción del recurso | (vacía) | (vacía) |

El dataset declara `accrualPeriodicity: R/P1M` (actualización mensual) y su `notes` no
menciona la existencia de las dos familias. La documentación oficial en GitHub
(`datosenergia/produccion-de-petroleo-y-gas-por-pozo`, revisión de 2019) describe un único
esquema —el que coincide con la familia normal, incluyendo ya `rectificado`, `habilitado` y
`fechaingreso`— sin mencionar la variante "abiertas y cerradas": es documentación vieja que
quedó desactualizada cuando la Secretaría empezó a publicar la segunda vista.

El dato más importante de la tabla de arriba: **la familia normal no se actualiza desde
marzo de 2026, cinco meses antes que la última actualización de la familia DDJJ**. Es
evidencia directa de que "normal" es una vista congelada y "DDJJ abiertas y cerradas" es la
que la Secretaría sigue refrescando.

## Tamaño y forma

| Métrica | Normal | DDJJ abiertas y cerradas |
| --- | ---: | ---: |
| Filas | 983.551 | 983.710 |
| Columnas | 39 (incluye `id`) | 38 |
| Pozos únicos (`idpozo`) | 82.379 | 82.379 |
| Duplicados `idpozo+anio+mes` | 0 | 0 |

Columnas: la única diferencia de esquema es `id` (bigint), presente solo en la familia
normal. Es la concatenación de `idpozo` + `anio` + `mesmes` (ej. `32173202401` para el pozo
32173, año 2024, mes 01): una clave sintética redundante con `idpozo+anio+mes`, sin
información nueva.

## Filas que están en una familia y no en la otra (clave `idpozo+anio+mes`)

| | Cantidad |
| --- | ---: |
| Claves solo en normal | 0 |
| Claves solo en DDJJ | 159 |
| Claves comunes | 983.551 |

Las 159 filas exclusivas de DDJJ son homogéneas: **todas** de SHELL ARGENTINA S.A., **todas**
de `anio=2024, mes=9`, **todas** con `rectificado='t'` y la misma `fechaingreso`
(2024-10-17 12:40:39). Sumadas declaran 157.933,37 m³ de petróleo y 16.174,04 Mm³ de gas que
la familia normal no tiene en absoluto para ese mes (no hay ni siquiera una versión sin
rectificar de esas 159 claves en `normal`: no es que se pisen valores, es que la fila no
existe). Por `tipoestado`, 118 de esas 159 son "Extracción Efectiva".

## Columnas que difieren en las 983.551 filas comunes

| Columna | Filas distintas |
| --- | ---: |
| `idempresa`, `prod_pet`, `prod_gas`, `prod_agua`, `iny_agua`, `iny_gas`, `iny_co2`, `iny_otro`, `tef`, `vida_util`, `tipoextraccion`, `tipoestado`, `tipopozo`, `observaciones`, `fechaingreso`, `rectificado`, `habilitado`, `idusuario`, `empresa`, `sigla`, `formprod`, `formacion`, `cuenca`, `tipo_de_recurso`, `proyecto`, `sub_tipo_recurso`, `fecha_data` | **0** |
| `profundidad` | 9 |
| `idareapermisoconcesion` / `areapermisoconcesion` | 159 |
| `idareayacimiento` / `areayacimiento` | 195 |
| `provincia` | 96 |
| `clasificacion` / `subclasificacion` | 16 |

Ninguna columna de producción, inyección, tiempo efectivo o estado del pozo difiere en una
sola fila entre las dos familias. Lo que sí difiere son columnas de metadata administrativa
en 15 pozos (de 82.379), y son correcciones de catálogo, no errores:

- `areapermisoconcesion`: renombres de concesión (ej. "MESETA BUENA ESPERANZA" →
  "MESETA BUENA ESPERANZA I" / "II", "LAS TACANAS" → "LAS TACANAS I" / "II", "AGUADA
  VILLANUEVA" → "AGUADA VILLANUEVA NORTE") repetidos en los 12 meses del pozo — un cambio de
  catálogo aplicado retroactivamente a todo 2024 en la corrida más nueva (DDJJ).
- `provincia`: 96 filas de pozos que pasan de "Neuquén" a "Río Negro" — una corrección de
  límite jurisdiccional.
- `profundidad`: 1 pozo (9 filas, una por mes con datos) corrige 4,635 → 4.635 (factor 1.000:
  probable error de unidad, km vs. m, corregido en DDJJ).
- `clasificacion`/`subclasificacion`: 2 pozos pasan de EXPLOTACION a EXPLORACION.

Es exactamente lo esperable de una vista que se sigue actualizando (DDJJ) contra una vista
congelada (normal): el catálogo de áreas, provincias y clasificación se corrige con el
tiempo y la corrida más nueva lo refleja.

## Interpretación

**¿"DDJJ abiertas y cerradas" incluye declaraciones cerradas además de abiertas?** Sí, y es
literal: para las 983.551 declaraciones que ya cerraron su ciclo, DDJJ trae exactamente los
mismos valores que la familia normal (0 diferencias en todas las columnas de producción y
estado). Además trae 159 declaraciones que fueron reabiertas por una rectificación posterior
(`rectificado='t'`) y que la familia normal —al no seguir actualizándose— nunca llegó a
incorporar.

**¿Cuál es la vista vigente?** DDJJ abiertas y cerradas. La familia normal quedó congelada en
marzo de 2026; DDJJ se actualizó por última vez en agosto de 2026 y es la que sigue
recibiendo rectificativas.

## Recomendación

**Usar la familia "DDJJ abiertas y cerradas"**, confirmando la elección provisoria de
`pipelines/ingest/datasets.yaml`. Motivos:

1. Es un superconjunto estricto de la familia normal: nunca tiene menos filas ni valores
   distintos en lo que comparte, solo agrega 159 filas y actualiza metadata de catálogo.
2. Está activamente mantenida (último refresh agosto 2026) mientras la normal está congelada
   (marzo 2026): con el tiempo la brecha solo puede crecer.
3. Las filas exclusivas son producción real de pozos ya conocidos (no pozos nuevos ni ruido):
   omitirlas subestima la producción de septiembre 2024 para esos pozos.
4. El costo de elegirla es nulo: no trae la columna `id` (redundante, no se usa en el
   contrato) y pesa 12 MB menos por año.

**Implicancia para silver**: no hace falta cambiar el contrato
(`pipelines/contracts/produccion_pozo.yaml`). El esquema es idéntico entre familias salvo
`id`, que ya no se ingiere. `dedupe_by: fechaingreso` sigue siendo la regla correcta: en 2024
no hubo duplicados de `idpozo+anio+mes` dentro de una misma familia, pero sí pueden aparecer
entre corridas de ingesta sucesivas si una DDJJ se rectifica más de una vez después de
cargada — el mecanismo ya contempla ese caso general.
