# ADR 0003 — Gold se modela con dbt y la ejecuta Athena

**Estado:** aceptada · 2026-09-06

## Contexto

Bronze y silver son jobs de PySpark: leen una tabla, aplican reglas y escriben otra (ADR 0002).
Gold es distinto. Son nueve modelos dimensionales que dependen unos de otros —cuatro
dimensiones, tres tablas de hechos y dos marts—, cada uno con su documentación y sus tests, y lo
que hay que poder contestar es "de dónde sale esta columna". Además `dim_pozo` es una SCD tipo
2 sobre 21 años de declaraciones: hay que generar tramos de vigencia y verificar que no se
solapen. Escribir eso como otro job de PySpark significaría reimplementar a mano el grafo de
dependencias, el orden de ejecución, la documentación y los tests: exactamente lo que dbt hace
y hace bien.

Quedaban dos preguntas: qué motor ejecuta el SQL de los modelos y en qué proceso corre dbt.

## Decisión

**dbt con el adaptador `dbt-athena`.** El motor ya estaba elegido (ADR 0001): Athena lee y
escribe las mismas tablas Iceberg del Glue Data Catalog que produce silver, no hay nada que
levantar y se paga por TB escaneado. La alternativa era `dbt-glue`, que lanza una sesión
interactiva de Glue por corrida: más caro y un motor más que mantener para el mismo resultado.

Los tests van con los modelos: `unique`, `not_null` y `relationships` en los YAML, más nueve
tests singulares en SQL (grano único de dos hechos y de la curva tipo, vigencias de `dim_pozo`
sin solapamiento, arena por etapa, cocientes y medidas no negativas, la cohorte igual al año de
la primera producción y una reconciliación de la producción 2024 contra silver). Son 90 tests
que corren en el mismo `dbt build` que construye las tablas, así que una tabla mal construida
no queda publicada en silencio.

**Un job de Glue `gold_dbt` que corre `dbt build --target aws`, sobre Glue 5.0 (Spark) y no
sobre Python shell, aunque Spark no se use.** dbt necesita un proceso donde correr y el
proyecto no tiene ni una máquina ni un contenedor propio en AWS. Glue Python shell sigue
clavado en Python 3.9; dbt-core lo dejó de soportar en la 1.11 y `dbt-athena` en la 1.10.2. En
3.9 lo más nuevo que resuelve pip es `dbt-athena` 1.9.5 con un `dbt-core` de dos versiones
menores atrás, solo para ahorrar unos centavos. Glue 5.0 trae Python 3.11, así que el mismo
tipo de job que ya usan bronze y silver corre las versiones actuales. Se paga un clúster de dos
DPU que dbt no toca —el trabajo lo hace Athena— y son unos 0,15 USD por corrida.

El proyecto de dbt viaja adentro del wheel (`pipelines/dbt/`), que Glue instala con pip junto
con `dbt-core` y `dbt-athena`; el wrapper `pipelines/aws/gold_dbt_job.py` traduce los argumentos
del job a las variables que lee el target `aws` de `profiles.yml` e invoca a `dbtRunner`. La
máquina de estados `gold_mensual` lo dispara igual que las de las fuentes disparan sus jobs.

La otra alternativa era correr dbt desde GitHub Actions con credenciales OIDC. Es más barato
todavía (cero), pero saca a gold de Step Functions: el pipeline quedaría partido entre dos
orquestadores, `aws_logs.ps1` no la vería y no habría forma de encadenar gold detrás de las
fuentes. La comodidad de tener una sola máquina de estados vale más que 15 centavos.

**Las funciones de Trino con nombre corto viven en `macros/funciones_trino.sql`.** Athena es
Trino, y ocho operaciones frecuentes se escriben ahí de forma larga o poco evidente:
`md5` toma y devuelve varbinary, no hay forma de armar una fecha desde tres enteros, `unnest` va
en el `FROM` y no en el `SELECT`, no existe `median` y la mediana es `approx_percentile`. Cada
una es una macro de tres líneas, con su porqué al lado, para que el modelo se lea por lo que
hace y no por cómo se deletrea; tres de ellas —`fin_de_mes`, `dias_entre` y `meses_entre`— no
llevan el nombre de la función de Trino porque dbt-core ya publica macros `last_day` y
`datediff` propias y redefinirlas se las cambiaría también a dbt.

Con un solo motor el archivo podría no existir, pero sigue ganándose el lugar por la misma razón
que `macros/claves.sql`: hay expresiones que tienen que escribirse una sola vez o dejan de
coincidir. La clave surrogate de empresa se calcula en `dim_empresa` y en las tablas de hechos, y
si el `md5` o la normalización del nombre difirieran en un carácter la fact apuntaría a una
empresa que no existe; el calendario de `dim_fecha` arma fechas y series de meses en varios
lugares del mismo modelo. Una definición por operación, y los modelos se leen en SQL estándar.

## Consecuencias

- Los modelos quedan como tablas Iceberg (`table_type: iceberg`, `format: parquet` en
  `dbt_project.yml`): las lee Athena y las lee cualquier job de Spark.
- Los datos de las tablas de gold van a `warehouse/gold/` (`s3_data_dir`) y no debajo de
  `athena-results/`, que es donde dbt-athena los pondría por defecto y donde la regla de ciclo
  de vida del bucket los borraría a los siete días.
- El job de gold instala unos cincuenta paquetes en cada corrida (dbt y el wheel con sus
  dependencias). Medido en `prod` el 2026-09-12: 4 minutos de job para los 8 modelos y sus
  tests, todos en verde. Buena parte es arranque del clúster e instalación, no `dbt build`. Es
  el precio de no mantener una imagen propia.
- Un modelo nuevo no necesita `terraform apply`: el proyecto de dbt viaja en el wheel, así que
  alcanza con volver a correr `scripts/aws_deploy.ps1`.
- `dbt docs` no se genera. El catálogo y el manifiesto quedarían en el disco efímero del job de
  Glue, sin nadie que los sirva.
