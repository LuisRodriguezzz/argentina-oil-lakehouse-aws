# Fuente: producción mensual de pozos de gas y petróleo (Capítulo IV)

Dataset `produccion-de-petroleo-y-gas-por-pozo` del portal de la Secretaría de Energía. El
portal publica cada año en dos familias de CSV ("normal" y "DDJJ abiertas y cerradas"); la
comparación completa y la recomendación de cuál usar están en
[`comparacion-familias-produccion.md`](comparacion-familias-produccion.md). Este documento
describe la familia elegida, **DDJJ abiertas y cerradas**, que es la que carga
`lake.bronze/silver.produccion_pozo`.

**Qué es**: una fila por pozo × mes × año con la declaración jurada (DDJJ) de producción e
inyección presentada por la operadora. Es el lado "resultado" del que
[`fractura.md`](fractura.md) describe "cómo se estimuló el pozo"; se cruzan por `idpozo`.

Medido sobre el año completo 2024 (`data/raw/comparacion/ddjj_2024.csv`) el 2026-09-06:
**983.710 filas, 38 columnas, 82.379 pozos únicos, 59 empresas**. La tabla completa del
lakehouse (2006-2026, todos los años con esta misma familia) tiene 18.218.514 filas (ver
README).

## Columnas

| Columna | Tipo | Significado |
| --- | --- | --- |
| `idempresa` | string | Código interno de la empresa declarante |
| `anio` | int | Año de la declaración jurada; columna de partición |
| `mes` | int | Mes de la declaración jurada (1-12) |
| `idpozo` | bigint | Identificador único del pozo. **Clave primaria** junto con `anio`/`mes`; cruza con `pozo_primera_produccion.idpozo` y `fractura.idpozo` |
| `prod_pet` | double | Producción de petróleo del mes, en m³ |
| `prod_gas` | double | Producción de gas del mes, en Mm³ (miles de m³) |
| `prod_agua` | double | Producción de agua del mes, en m³ |
| `iny_agua` | double | Inyección de agua del mes (recuperación secundaria), en m³ |
| `iny_gas` | double | Inyección de gas del mes, en Mm³ |
| `iny_co2` | double | Inyección de CO2 del mes, en Mm³ |
| `iny_otro` | double | Inyección de otros fluidos del mes |
| `tef` | double | Horas efectivas de producción en el mes; rango teórico 0-744 (744 = 31 días × 24 h) |
| `vida_util` | double | Vida útil estimada del pozo; vacía en 2024 (ver rarezas) |
| `tipoextraccion` | string | Sistema de extracción declarado (8 valores) |
| `tipoestado` | string | Estado del pozo en el mes (17 valores) |
| `tipopozo` | string | Destino del pozo (Petrolífero, Gasífero, Inyección de Agua, etc.; 8 valores) |
| `observaciones` | string | Texto libre cargado por la empresa; 92,4 % nulo |
| `fechaingreso` | timestamp | Momento en que la declaración entró al sistema; desempata rectificativas (`dedupe_by` del contrato) |
| `rectificado` | boolean | Llega como `t`/`f`; la declaración rectifica una anterior |
| `habilitado` | boolean | Llega como `t`/`f`; declaración habilitada para publicación |
| `idusuario` | string | Usuario que cargó la declaración |
| `empresa` | string | Razón social de la empresa declarante (59 valores en 2024) |
| `sigla` | string | Identificador legible del pozo, usado en la industria |
| `formprod` | string | Código de la formación productiva |
| `profundidad` | double | Profundidad del pozo, en metros |
| `formacion` | string | Nombre de la formación productiva |
| `idareapermisoconcesion` | string | Código del área de permiso o concesión |
| `areapermisoconcesion` | string | Nombre del área de permiso o concesión |
| `idareayacimiento` | string | Código del yacimiento |
| `areayacimiento` | string | Nombre del yacimiento |
| `cuenca` | string | Cuenca sedimentaria (9 valores) |
| `provincia` | string | Provincia donde está el pozo |
| `tipo_de_recurso` | string | CONVENCIONAL / NO CONVENCIONAL / SIN RESERVORIO / NO DISCRIMINADO |
| `proyecto` | string | Proyecto al que se imputa la producción |
| `clasificacion` | string | EXPLOTACION / EXPLORACION / SERVICIO / ALMACENAMIENTO |
| `subclasificacion` | string | Subclasificación del pozo (8 valores) |
| `sub_tipo_recurso` | string | SHALE o TIGHT; solo aplica al no convencional |
| `fecha_data` | date | Último día del mes declarado |

La familia "normal" trae además una columna `id` (bigint: `idpozo` concatenado con
`anio`+`mes`) que no se ingiere — ver `comparacion-familias-produccion.md`.

## Clave

`idpozo + anio + mes`. Sin duplicados en 2024 (0 grupos con más de una fila, en ninguna de
las dos familias). El contrato usa `dedupe_by: fechaingreso` para el caso general en que una
misma clave aparezca más de una vez entre corridas de ingesta sucesivas (una DDJJ rectificada
más de una vez): gana la de `fechaingreso` más alta.

## Cadencia

Mensual (`accrualPeriodicity: R/P1M` a nivel dataset en CKAN). La familia DDJJ abiertas y
cerradas es la que la Secretaría sigue refrescando activamente (último `last_modified` de
CKAN medido: 2026-08-04); la familia normal quedó congelada en 2026-03-03.

## Rarezas medidas (2024, familia DDJJ abiertas y cerradas)

- **`tef`**: mínimo -0,01 (1 fila negativa, viola el `min: 0` del contrato → cuarentena),
  máximo 720 (no llega al techo teórico de 744), mediana 0.
- **`vida_util`**: prácticamente vacía en 2024: 958.234 filas nulas y las 25.476 restantes en
  0. No usar como feature sin verificar que otros años la traigan poblada.
- **`rectificado`**: 159 filas en `t` sobre 983.710 (0,016 %); las 159 son de SHELL ARGENTINA
  S.A., todas de `anio=2024, mes=9` — el detalle está en `comparacion-familias-produccion.md`.
- **`profundidad`**: 121.355 filas en 0 (pozo sin dato o boca de pozo sin profundidad
  registrada). Un pozo (`YEA.RN.EFO-152(d)`, YPF S.A., cuenca Neuquina) declara 378.939
  metros en sus 12 filas de 2024 — claramente un error de carga (el pozo más profundo del
  mundo ronda los 12 km). Otros 6 pozos superan los 10.000 m; el contrato no pone techo a
  `profundidad`, así que estas filas no van a cuarentena hoy.
- **`tipoestado`** dominante: Extracción Efectiva 321.242 (32,6 %), Abandonado 224.204
  (22,8 %); 38 filas nulas. Un tercio de las filas son pozos que no producen ese mes.
- **`cuenca`**: Golfo San Jorge 511.793 (52,0 %), Neuquina 378.465 (38,5 %), Cuyana 44.111,
  Austral 37.207, Noroeste 11.930, y tres cuencas marginales (Noreste 132, Ñirihuau 24,
  Cañadón Asfalto 12) más 36 filas nulas.
- **YPF S.A.**: 471.757 filas (48,0 % del total), 39.787 pozos, sobre 59 empresas
  declarantes.
- **`tipo_de_recurso`**: Convencional 930.373 (94,6 %), No convencional 48.589 (4,9 %), Sin
  reservorio 4.688, No discriminado 60 — coincide exactamente con los `allowed_values` del
  contrato.
- **`clasificacion`/`subclasificacion`**: 201.180 filas nulas en ambas (20,4 %) —
  declaraciones sin ese campo cargado.

## Decisiones del contrato

- Se elige la familia DDJJ abiertas y cerradas sobre la normal (pendiente 5 de semana 0,
  resuelto); no se ingiere la columna `id`, exclusiva de la familia normal y redundante con
  la clave.
- `tef` acotado a `[0, 744]`: el valor negativo medido (-0,01) cae en cuarentena por el
  mínimo, no por el máximo.
- `prod_*` e `iny_*` con `min: 0`.
- `tipo_de_recurso` con `allowed_values` cerrado a los 4 valores medidos.
- `dedupe_by: fechaingreso` para resolver rectificativas que compartan clave entre corridas.
