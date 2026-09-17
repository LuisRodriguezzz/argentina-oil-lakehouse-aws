# Gold: el modelo dimensional con dbt

Nueve modelos sobre las cuatro tablas de silver, construidos con dbt y ejecutados por Athena
(ADR 0003). Todos son tablas Iceberg en Parquet que se reconstruyen enteras en cada corrida:
silver reescribe particiones de años viejos cuando el portal rectifica, y un incremental se
perdería esas correcciones. Cada modelo tiene su `.yml` al lado con la descripción de cada
columna y sus tests.

## Modelos

| Modelo | Grano | Qué aporta |
| --- | --- | --- |
| `dim_fecha` | un mes, 2006 a diciembre del año que viene | calendario generado, sin agujeros |
| `dim_empresa` | una operadora | nombre normalizado y clave surrogate |
| `dim_yacimiento` | un yacimiento | cuenca y provincia |
| `dim_pozo` | un pozo × un tramo de vigencia (SCD tipo 2) | los atributos del pozo en cada época, `primera_produccion` |
| `fact_produccion_mensual` | un pozo × un mes declarado | producción e inyección, edad del pozo en meses |
| `fact_fractura` | una declaración de fractura | el diseño de la estimulación, arena por etapa |
| `fact_reservas` | un yacimiento × año × categoría | reservas y recursos al 31/12 |
| `mart_pozo_completacion_produccion` | un pozo fracturado | su completación y su producción a 3, 6 y 12 meses, normalizada por metro y por etapa |
| `mart_curva_tipo` | cohorte × formación × subtipo × tipo de pozo × mes de vida | la curva tipo: pozos, media y mediana del acumulado |

Las claves surrogate (`empresa_key`, `yacimiento_key`, `pozo_key`) son `md5` de la clave
natural, calculados con las macros de `macros/claves.sql`: la misma expresión en la dimensión
y en los hechos, o dejan de coincidir. Las funciones de Trino que se escriben de forma larga
(`md5`, armar una fecha desde enteros, `unnest`, la mediana con `approx_percentile`) tienen un
nombre corto en `macros/funciones_trino.sql`.

## Tres definiciones que conviene conocer

- **Primera producción** de un pozo: el primer mes en que declaró petróleo o gas mayor a cero.
  Se calcula en `dim_pozo` desde las declaraciones, no desde el padrón oficial: el padrón solo
  trae el mes real para el año en curso y pisa los demás con enero
  ([ficha](../../docs/fuentes/pozo_primera_produccion.md)). La fecha del padrón queda como
  `primera_produccion_padron`, para cruzar.
- **Completación** de un pozo, en el mart: el trabajo de fractura anterior a su primera
  producción, con las declaraciones de un mismo trabajo consolidadas (el Adjunto IV puede traer
  un trabajo partido en varias filas, y un pozo puede re-fracturarse años después). La columna
  `completacion_anterior_a_produccion` marca los pozos que ya producían cuando se fracturaron:
  para ellos el diseño no explica la producción temprana.
- **`idpozo`** identifica pozo × formación productiva; una misma sigla puede tener varios.
  "Cantidad de pozos" en gold cuenta `idpozo`.

## Tests

94 tests corren dentro del mismo `dbt build` que construye las tablas, así que una tabla mal
construida no queda publicada en silencio. Los genéricos (`unique`, `not_null`,
`relationships`, `accepted_values`) están en los `.yml`; los once singulares de `tests/` cubren
lo que un test de una columna no puede: grano único de claves compuestas, tramos de `dim_pozo`
sin solapamiento, cocientes no negativos, la definición de primera producción y de
completación, y una reconciliación de la producción 2024 contra silver.

## Cómo corre

- **En AWS**: el job de Glue `gold_dbt` corre `dbt build --target aws`. El proyecto viaja
  adentro del wheel del repo; el wrapper `pipelines/aws/gold_dbt_job.py` traduce los argumentos
  del job a las variables de entorno que lee `profiles.yml`. Lo dispara la máquina de estados
  `gold_mensual`. Un modelo nuevo no necesita Terraform: alcanza con el despliegue del wheel.
- **En una máquina de desarrollo**, sin conectarse a Athena:

  ```powershell
  uvx --from "dbt-core==1.11.14" --with "dbt-athena==1.11.0" dbt parse --profiles-dir pipelines/dbt --project-dir pipelines/dbt
  ```

  `parse` compila el proyecto y atrapa un `ref()` a un modelo que no existe, un `.yml` que
  documenta una columna de un modelo que ya no está o una macro mal escrita. No valida el SQL
  contra el motor: eso pasa recién en el `build`. Necesita las seis variables de entorno de
  `profiles.yml` con cualquier valor (ver `.github/workflows/ci.yml`).

Los datos de las tablas van a `warehouse/gold/` del bucket; los resultados de las consultas, a
`athena-results/`, que se vacía a los siete días. `dbt docs` no se genera.
