# Jobs de Spark

Bronze y silver del lakehouse. Corren como jobs de Glue 5.0 (`glueetl`); los wrappers de
`pipelines/aws/` traducen los argumentos del job a variables de entorno y llaman a `main()`.
No hay forma de correrlos fuera de AWS: el catálogo `lake` es el Glue Data Catalog.

## bronze_load

Copia los CSV crudos de `s3://<bucket>/landing/` a tablas Iceberg `lake.bronze.*`, una
partición por recurso (`_resource_id`). Qué hace, en orden:

1. Lee del manifiesto de ingesta (Postgres, por JDBC) la última corrida `ok` de cada recurso.
2. Resuelve la tabla destino de cada recurso con `bronze_tables.yaml` (regex sobre el nombre).
3. Lee de cada tabla bronze qué recursos ya están cargados y con qué `_source_sha256`.
4. Carga solo los recursos nuevos o cuyo hash cambió. Correrlo dos veces no hace nada.
5. Por cada recurso: lee el CSV con `header=true` y todas las columnas string, agrega las
   columnas de linaje (`_resource_id`, `_source_key`, `_source_sha256`, `_ingest_date`,
   `_loaded_at`, `data_origin`) y reemplaza la partición del recurso.

## silver_load

Aplica un contrato de datos (`pipelines/contracts/*.yaml`, ADR 0002) sobre una tabla bronze y
escribe `lake.silver.*` tipada y particionada. Qué hace, por recurso pendiente:

1. Compara `_resource_id -> _source_sha256` entre bronze y silver: procesa solo lo nuevo o
   cambiado, igual que bronze.
2. Marca cada fila con `reject_reason` según los `min`/`max`/`allowed_values` del contrato.
   Las que violan algo van a `lake.silver.<tabla>_rejects` con sus strings originales.
3. Castea las filas que quedan al tipo del contrato, conserva el linaje y agrega
   `_silver_loaded_at`.
4. Deduplica por `primary_key` quedándose con la fila de `dedupe_by` más alto.
5. Corre los checks duros (nulos donde el contrato no los permite, columnas ausentes, clave
   duplicada, más de 1 % de rechazos): si alguno falla, no escribe y el job devuelve 1.
6. Reemplaza las particiones afectadas y registra la corrida en `lake.silver.dq_runs`.

## Cómo se invocan en AWS

Las máquinas de estados de `infra/terraform/stepfunctions.tf` encadenan ingesta, bronze, silver
y gold, y pasan `--dataset` y `--contract` en cada paso. A mano:

```bash
aws stepfunctions start-execution --state-machine-arn <arn> --input '{}'   # pipeline entero
aws glue start-job-run --job-name silver_load_dev --arguments '{"--contract": "fractura"}'
```

Los dos jobs aceptan además `--resource-id` para reprocesar un solo recurso sin tocar el resto
de la tabla. El input de la ejecución acota la corrida sin tocar la definición: con
`{"ingesta": {"--only": "^Padr"}}` la ingesta baja solo los recursos que matchean.

Los wrappers son `pipelines/aws/bronze_job.py` (job `bronze_load<sufijo>`) y
`pipelines/aws/silver_job.py` (job `silver_load<sufijo>`). Cada uno tiene un argumento propio
(`--dataset` y `--contract`); el resto (`--GLUE_WAREHOUSE`, `--GLUE_DATABASE_SUFFIX`,
`--S3_LANDING_BUCKET`, `--S3_REGION`, `--POSTGRES_DSN_SSM_PARAMETER`) son los argumentos por
defecto que pone Terraform. El DSN de Postgres llega por SSM, nunca en claro, y solo a bronze:
silver no toca landing ni el manifiesto.

Para mirar el resultado, `uv run python scripts/check_lake.py --namespace silver --suffix _dev`
muestra filas por partición, el último snapshot de cada tabla, `dq_runs` y la cuarentena
agrupada por motivo.

## Decisiones

- **Bronze no tipa.** Todo entra como string y se conserva tal cual, filas basura incluidas:
  si bronze tipa, un CSV mal formado se pierde antes de que alguien pueda auditarlo. El casteo
  y las reglas de calidad son de silver, y ahí el YAML manda (ADR 0002).
- **Idempotencia por hash, no por fecha.** El manifiesto ya distingue contenido nuevo de
  contenido repetido; los dos jobs comparan el `sha256` cargado contra el del origen.
- **Una partición por recurso.** `overwritePartitions()` reemplaza el año que se recarga sin
  tocar el resto, y `write.spark.accept-any-schema` + `merge-schema` tolera que un año traiga
  columnas que otro no tiene (2006 y 2024 no comparten esquema exacto).
- **Una tabla por tipo de recurso** (`bronze_tables.yaml`): `produccion_pozo` mezcla los
  anuales de DDJJ con "No Convencional" (un subconjunto: en la misma tabla duplicaría filas),
  "Capítulo IV - Pozos" (catálogo) y el padrón de primera producción (tres columnas). Un
  recurso que no matchea ningún patrón se saltea con un WARNING: preferimos no cargarlo a
  cargarlo en la tabla equivocada.
- **El manifiesto se lee por JDBC**, no con SQLAlchemy: Glue instala el wheel con `--no-deps`
  y el runtime ya trae el driver de Postgres para Spark.
- **BOM.** Los CSV del portal son UTF-8 con BOM y Spark no lo saca: el nombre de la primera
  columna se limpia a mano (`clean_column_name`).
- **Las funciones puras viven en `bronze_rules.py` y `silver_rules.py`**: las expresiones de
  casteo y de rechazo son strings de SQL, así que `tests/spark_jobs/` las compara de a una sin
  levantar Spark. La integración se valida corriendo el job en Glue.
