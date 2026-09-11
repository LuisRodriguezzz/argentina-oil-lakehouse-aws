# Fuente: reservas y recursos por yacimiento

La Secretaría de Energía publica una vez al año un ZIP con las **reservas y recursos
declarados al 31 de diciembre**, yacimiento por yacimiento. Es la foto patrimonial del
upstream: `produccion_pozo` cuenta lo que se extrajo y esta fuente cuánto queda por
extraer. No está en CKAN, es un archivo suelto por URL (`reservas_al_31-12-{año}.zip`), y
verificado por HEAD el 2026-09-05 solo están publicados **2020 a 2024**.

Cada ZIP (314-360 KB) trae un único XLSX con **dos hojas**, que son dos horizontes de la
misma estimación: `fin de concesión` (hasta que vence el contrato) y `fin de vida útil`
(hasta que el yacimiento se agota). La segunda siempre es mayor o igual que la primera.

## Cómo es el Excel

No es una tabla: es un cuadro de doble entrada con **7 filas de encabezado** y rangos
fusionados en cuatro niveles sobre 29 columnas.

| Fila | Qué trae |
| --- | --- |
| 1-2 | Título fusionado (`RESERVAS Y RECURSOS AL 31/12/2024 - HASTA EL FIN DE LA CONCESIÓN`) |
| 3 | `CONVENCIONAL` · `NO CONVENCIONAL` · `CONVENCIONAL + NO CONVENCIONAL` (8 columnas cada uno) |
| 4 | `RESERVAS` (6 columnas) · `RECURSOS CONTINGENTES` (2 columnas, fusionadas con la fila 5) |
| 5 | `COMPROBADAS` · `PROBABLES` · `POSIBLES` (2 columnas cada una) |
| 6 | `PET` · `GAS` |
| 7 | `OPERADOR, CUENCA, PROVINCIA, CONCESIÓN O PERMISO, YACIMIENTO` y las unidades `(Mm3)` / `(MMm3)` |

Las 24 columnas de valores son 3 bloques de 8. El tercero,
`CONVENCIONAL + NO CONVENCIONAL`, es la **suma de los otros dos**: es un total derivable,
no un dato nuevo, así que no se carga (guardarlo además rompería la unicidad de la clave).

## Diferencias entre años

El layout es sorprendentemente estable: los 10 encabezados (5 archivos × 2 hojas) tienen
las mismas 19 celdas fusionadas y las mismas 29 columnas. Lo que cambia:

- **Nombres de hoja**: 2020 rotula `Fin Concesion` y `Fin de vida útil`; 2021-2024,
  `fin de concesión` y `fin de vida util`. Cambian mayúsculas, acentos y la preposición.
- **Fila de totales**: en 2021-2024 el `TOTAL` es la última fila, después de una vacía. En
  2020 está en la fila 1246 y le siguen 4-5 filas vacías, y las columnas A-E del total
  están fusionadas (`A1246:E1246`), cosa que no pasa en los otros años.
- **Filas del cuadro** (por hoja): 1.237 en 2020, 1.239 en 2021, 1.256 en 2022, 1.247 en
  2023 y 1.234 en 2024.
- **Orden**: 2020 y 2021 vienen ordenados por concesión; 2022-2024, por operador.
- **Celdas vacías**: 678 en 2024 y 104 en 2023, sobre ~39.500 valores. Una celda vacía no
  es un cero declarado, así que llega a silver como `NULL` y no como `0`.
- **Cuencas nuevas**: `ARGENTINA NORTE` (offshore, 160 filas) aparece recién en los últimos
  años; `ÑIRIHUAU` (128) y `NORESTE` (64) son marginales pero constantes.

## Decisiones del parser (`pipelines/reservas/parser.py`)

- **Los rangos fusionados se propagan hacia la derecha, nunca hacia abajo.** Un rótulo
  fusionado a lo ancho encabeza todas esas columnas. Uno fusionado a lo alto
  (`RECURSOS CONTINGENTES` ocupa la fila de categoría *y* la de certeza) significa que ese
  bloque **no se subdivide**, no que la subdivisión se llame igual que el bloque.
- **Los niveles se ubican por vocabulario, no por posición**: la fila de encabezado es la
  que dice `OPERADOR`, y de las de arriba se reconoce cuál es cuál por lo que contienen
  (`PET`/`GAS`, `COMPROBADAS`…). Un título de más en alguna edición no corre los niveles.
- **Las filas de `TOTAL` se descartan** (2 por archivo, una por hoja) y quedan contadas en
  el log de la carga. Las filas separadoras vacías también.
- **`certeza` = `no_aplica`** en los recursos contingentes, en vez de vacío: es parte de la
  clave primaria y una clave con nulos no es clave (además, `count_distinct` de Spark
  descarta las filas con algún nulo, así que un nulo ahí haría fallar el chequeo de
  unicidad del contrato).
- **Bronze no tipa**: `valor` y `anio_corte` llegan como texto, igual que en el bronze de
  Spark. El año sale del nombre del archivo.

## Bronze sin Spark

`pipelines/reservas/bronze_load.py` escribe `lake.bronze.reservas` con **pyiceberg** desde un
job de Glue Python shell (`bronze_reservas`), no desde uno de Spark. El archivo pesa 400 KB y
el trabajo real es desarmar un cuadro de Excel, algo que Spark no sabe leer: levantar un
clúster de Glue 5.0 para 40.000 filas sería pagar dos DPU por nada. La tabla que queda es
indistinguible de las que escribe Spark (mismas 6 columnas de linaje, misma partición por
`_resource_id`, misma idempotencia por `sha256`), y por eso silver corre con el job de
siempre: `silver_load.py --contract reservas`.

## Clave y una fila larga

`operador + cuenca + provincia + concesion + yacimiento + hoja + tipo_recurso + categoria +
certeza + fluido + anio_corte` identifica una fila. Se verificó sobre los 5 años: es única
salvo por **3 yacimientos declarados dos veces en la misma planilla** (CGC en
`ESTANCIA LIBRUN` y `LA MENOR` en 2020, ROCH en `LA CARMEN / POZOS SIN YACIMIENTO` en 2022).
No son datos distintos: la declaración viene partida entre las dos filas, donde una trae el
número la otra viene vacía, y todos los valores involucrados son 0. Por eso el contrato
deduplica con `dedupe_by: valor`, que se queda con el valor más alto del par.

Medido el 2026-09-05: **198.816 filas en bronze** (5 particiones) y **198.734 en silver**
(80 duplicadas por lo anterior y 2 rechazadas). La cuarentena tiene 2 filas, ambas de PAE en
el yacimiento `BAYO` en 2024, con `-0,01` en una columna de recursos contingentes: un
volumen negativo no existe, así que el contrato las manda a `lake.silver.reservas_rejects`.

```text
operador  cuenca    provincia  concesion     yacimiento    hoja           tipo_recurso     categoria  certeza      fluido    unidad  valor  anio_corte
YPF S.A.  NEUQUINA  Neuquén    LOMA CAMPANA  LOMA CAMPANA  fin_concesion  no_convencional  reservas   comprobadas  petroleo  Mm3     7965   2024
```

Reservas comprobadas de petróleo de YPF al cierre 2024, hoja fin de concesión, por cuenca
(en Mm3, miles de m3): Neuquina 134.756 (de los cuales 129.768 son no convencionales),
Golfo San Jorge 7.137, Cuyana 124, Austral y Argentina Norte 0. El 96 % de las reservas
comprobadas de petróleo de la compañía es no convencional en la cuenca Neuquina.
