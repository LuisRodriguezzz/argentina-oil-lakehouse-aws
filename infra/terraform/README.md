# Infraestructura en AWS

Lo mínimo para correr las cuatro capas: un bucket S3, el Glue Data Catalog, cinco jobs de
Glue, una máquina de estados por pipeline y un workgroup de Athena. El porqué de cada
elección está en `docs/adr/`: ADR 0001 para Glue, Step Functions y Athena, ADR 0003 para
gold con dbt.

| Job | Tipo | Qué hace |
| --- | --- | --- |
| `ingest_landing` | Python shell 3.9, 1/16 DPU | Baja los recursos de un dataset a `landing/`. |
| `bronze_load` | Glue 5.0 Spark, N × G.1X | CSV de landing a las tablas Iceberg de bronze. |
| `bronze_reservas` | Python shell 3.9, 1 DPU | Parsea el XLSX anual de reservas y escribe `bronze.reservas` con pyiceberg. |
| `silver_load` | Glue 5.0 Spark, N × G.1X | Aplica un contrato y escribe la tabla silver. |
| `gold_dbt` | Glue 5.0, N × G.1X | `dbt build`; el SQL lo ejecuta Athena. |

`ingest_landing`, `bronze_load` y `silver_load` son genéricos: no tienen dataset ni contrato
en sus argumentos por defecto, se los pasa la máquina de estados. `bronze_reservas` recibe el
`--dataset` igual pero lo ignora, porque carga una sola tabla. Cada pipeline es una entrada del
mapa `local.pipelines` de `stepfunctions.tf` con su dataset, **sus contratos**, qué job usa
para bronze y su cron: agregar uno nuevo son seis líneas, no otra definición de estados. Un
pipeline con más de un contrato (producción carga también el padrón de pozos) genera un estado
`silver_<contrato>` por contrato, encadenados.

## Ambientes

Hay dos, `dev` y `prod`, en la misma cuenta (ADR 0005). Los mismos `.tf` para los dos: la
variable `environment` sufija el nombre de cada recurso y el aislamiento del state lo da un
**workspace de Terraform** por ambiente.

| | dev | prod |
| --- | --- | --- |
| Bucket | `oil-lakehouse-<cuenta>-dev` | `oil-lakehouse-<cuenta>-prod` |
| Bases de Glue | `bronze_dev`, `silver_dev`, `gold_dev` | `bronze_prod`, `silver_prod`, `gold_prod` |
| Jobs / máquinas | `bronze_load_dev`, `fractura_diaria_dev` | `bronze_load_prod`, `fractura_diaria_prod` |
| Roles / workgroup | `argentina-oil-lakehouse-glue-job-dev`, `oil-lakehouse-dev` | `…-prod` |
| Workers de Spark | 2 (el mínimo de Glue) | 4 |
| Schedules | deshabilitados | deshabilitados |
| DSN de Neon | `/oil-lakehouse/dev/postgres_dsn` (branch `dev`) | `/oil-lakehouse/prod/postgres_dsn` (branch `main`) |

`environment` **no tiene default**: siempre hay que pasar el `-var-file`. Y el workspace
tiene que coincidir con el ambiente del tfvars: `aws_s3_bucket.lakehouse` lleva una
`precondition` que corta el plan si no coinciden, con el comando que hay que correr.

El sufijo llega al código por una sola variable de entorno, `GLUE_DATABASE_SUFFIX`, que
Terraform pasa como argumento a cada job. La consumen tres lugares:

- los jobs de Spark, con `bronze_rules.with_suffix` sobre los nombres de tabla que leen de los
  YAML de contratos;
- `pipelines/reservas/bronze_load.py`, que compone el namespace a mano porque el nombre de su
  tabla es una constante del módulo y no sale de ningún YAML;
- dbt, en `profiles.yml` (`schema: "gold{{ env_var('GLUE_DATABASE_SUFFIX', '') }}"`) y en
  `models/sources.yml`.

El state es **local** todavía, un archivo por workspace en `terraform.tfstate.d/`. El bloque
de backend S3 está escrito y comentado en `versions.tf`, y el bucket y la tabla de locks
están definidos en [`bootstrap/`](bootstrap/README.md), que **no se aplicó**.

## Desplegar

```powershell
cd infra\terraform
terraform init
terraform workspace select -or-create dev      # o prod
terraform plan  -var-file=envs\dev.tfvars
terraform apply -var-file=envs\dev.tfvars      # 29 recursos
..\..\scripts\aws_deploy.ps1                   # uv build + sube wheel y wrappers a artifacts/
```

`aws_deploy.ps1` (y `aws_deploy.sh`, el mismo script para Git Bash y Linux) lee el bucket y el
ambiente de `terraform output` **del workspace seleccionado**: publica en el ambiente en el que
uno esté parado, y lo imprime antes de subir nada. Los jobs leen su script de `s3://<bucket>/artifacts/` en cada corrida: después de tocar
código alcanza con volver a correrlo, sin `terraform apply`. Si cambia la versión del
proyecto, actualizar también la variable `wheel_name`.

El proyecto de dbt (`pipelines/dbt/`) viaja adentro del wheel, así que `gold_dbt` también se
actualiza con `aws_deploy`: un modelo nuevo no necesita Terraform.

Requisito externo por ambiente: el parámetro SecureString
`/oil-lakehouse/<ambiente>/postgres_dsn` con la cadena de conexión al branch de Neon
correspondiente. Se crean a mano y no los maneja Terraform: son secretos.

```powershell
# Una vez por ambiente, con el DSN del branch de Neon que corresponda.
aws ssm put-parameter --name /oil-lakehouse/dev/postgres_dsn --type SecureString `
  --value "postgresql://..." --overwrite
```

## Despliegue automático (deshabilitado)

`.github/workflows/deploy.yml` hace, cuando está habilitado: `terraform plan` de dev en cada
PR que toque `infra/terraform/**` o `pipelines/**`, `apply` de dev en cada push a `main` con
subida del wheel, y un job de prod que depende del de dev y espera aprobación manual. Se
autentica con OIDC (`role-to-assume`), sin claves en los secretos del repo.

Está deshabilitado por diseño: todos los jobs llevan `if: vars.DEPLOY_ENABLED == 'true'` y esa
variable no existe. **No puede funcionar mientras el state sea local**: un runner de GitHub
arranca vacío, no ve `terraform.tfstate.d/` y creería que no existe nada. Los cinco pasos para
habilitarlo están en [`bootstrap/README.md`](bootstrap/README.md).

## Correr el pipeline

```powershell
$arn = (terraform output -json state_machine_arns | ConvertFrom-Json).fractura_diaria
# Corrida completa (el dataset y el contrato los pone la máquina de estados)
aws stepfunctions start-execution --state-machine-arn $arn --input '{}'
# Corrida acotada: se mezcla con los argumentos fijos de cada paso, no los reemplaza. Las
# claves son nombres de estados (`ingesta`, `bronze`, `silver_<contrato>`).
aws stepfunctions start-execution --state-machine-arn $arn --input '{\"ingesta\":{\"--only\":\"^Padr\"}}'
# Reprocesar un recurso suelto, sin la máquina de estados (bronze, silver y reservas)
aws glue start-job-run --job-name silver_load_dev --arguments '{"--contract":"fractura","--resource-id":"..."}'

aws stepfunctions describe-execution --execution-arn <arn de la ejecución>
aws glue get-job-runs --job-name bronze_load_prod --max-items 1
..\..\scripts\aws_logs.ps1 -Ambiente prod    # resumen de la última corrida de cada job
```

Las claves de `state_machine_arns` son los nombres sin sufijo (`fractura_diaria`) aunque la
máquina se llame `fractura_diaria_prod`: así estos comandos valen igual en los dos ambientes.
Los pipelines son `produccion_pozo_mensual`, `fractura_diaria`, `reservas_mensual` y
`gold_mensual`. Los jobs compartidos corren de a uno y el pipeline que llega segundo espera y
reintenta ([ADR 0001](../../docs/adr/0001-lakehouse-serverless-en-aws.md)).

Los schedules de EventBridge (uno por máquina) existen pero nacen **deshabilitados** en los
dos ambientes: nada queda corriendo solo y el costo en reposo es cero. Para habilitarlos,
cambiar `enable_schedule` en el tfvars del ambiente y volver a aplicar. Están escalonados para
no pelearse por los jobs compartidos: producción el día 1 a las 6, fractura todos los días a
las 7, reservas el día 1 a las 9 y gold el día 1 a las 12, cuando las tres fuentes ya
terminaron.

## Consultar en Athena

```powershell
aws athena start-query-execution --work-group oil-lakehouse-prod `
  --query-string "SELECT count(*) FROM silver_prod.pozo_primera_produccion"
aws athena get-query-results --query-execution-id <id>
```

El workgroup fuerza su propia ubicación de resultados (`athena-results/`, que se limpia a
los 7 días), así que no hace falta pasar `--result-configuration`. Las tablas de gold no
viven ahí: dbt las escribe en `warehouse/gold/` justamente para que la regla de ciclo de vida
no se las lleve.

## Destruir

```powershell
terraform workspace select dev
terraform plan -destroy -var-file=envs\dev.tfvars   # qué se lleva puesto, sin llevárselo
terraform destroy -var-file=envs\dev.tfvars
```

Son 29 recursos por ambiente. Borra el bucket **con todos los datos adentro**
(`force_destroy = true`): landing, las tablas Iceberg de bronze, silver y gold, los artefactos
y los resultados de Athena. También borra las tres bases del catálogo, los cinco jobs, las
máquinas de estados, los schedules, el workgroup y los tres roles de IAM. No toca el parámetro
de SSM ni la base de Neon: el manifiesto de ingesta sobrevive.

Destruir un ambiente no toca al otro: son dos states distintos.

## Reconstruir el entorno de cero

Es el procedimiento completo, el mismo que se corre después de un `destroy` o la primera vez.
Toma alrededor de una hora, casi toda esperando a que la ingesta baje los CSV, y cuesta menos
de 2 USD.

```powershell
cd infra\terraform
terraform workspace select prod
terraform destroy -var-file=envs\prod.tfvars     # ver arriba qué se lleva puesto
terraform apply   -var-file=envs\prod.tfvars
..\..\scripts\aws_deploy.ps1                     # wheel + wrappers a artifacts/

# Si el manifiesto de ingesta ya está en Neon pero landing quedó vacío, hay que olvidarlo
# para que la ingesta vuelva a bajar los archivos.
# (Desde el host, con el DSN del branch de Neon del ambiente en el entorno.)
uv run python -c "from pipelines.ingest.manifest import Manifest, ingestion_manifest; import os; m = Manifest(os.environ['POSTGRES_DSN']); c = m.engine.connect(); c.execute(ingestion_manifest.delete()); c.commit()"

$maquinas = terraform output -json state_machine_arns | ConvertFrom-Json
foreach ($nombre in "produccion_pozo_mensual", "fractura_diaria", "reservas_mensual", "gold_mensual") {
  aws stepfunctions start-execution --state-machine-arn $maquinas.$nombre --input '{}'
  # Esperar a que termine antes de la siguiente: los jobs no corren en paralelo.
}
```

El orden importa: `gold_mensual` al final, porque `mart_pozo_completacion_produccion` cruza
producción con fractura y con el padrón de pozos. Si alguna fuente falta, el mart sale corto
y los tests de relación entre hechos y dimensiones lo delatan.

Verificación, con `scripts/aws_logs.ps1` para los logs y con Athena para las filas:

```sql
-- Conteos medidos el 2026-09-06 con este mismo código en `ypf-data-platform`, el proyecto
-- del que deriva este repo (un despliegue único, con las bases sin sufijo). En dev, fractura
-- dio el mismo resultado el 2026-09-12; el resto sigue pendiente de la primera carga de prod.
-- Fractura y producción crecen con cada republicación del portal; reservas y el mart no,
-- porque el ZIP anual y el padrón de pozos ya están cerrados.
SELECT count(*) FROM silver_prod.produccion_pozo;          -- 18.218.514
SELECT count(*) FROM silver_prod.pozo_primera_produccion;  --     86.197
SELECT count(*) FROM silver_prod.fractura;                 --      4.878
SELECT count(*) FROM silver_prod.reservas;                 --    198.734
SELECT count(*) FROM gold_prod.mart_pozo_completacion_produccion;  -- 4.635
```
