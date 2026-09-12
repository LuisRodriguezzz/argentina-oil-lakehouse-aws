# Infraestructura en AWS — runbook

Lo mínimo para correr las cuatro capas: un bucket S3, el Glue Data Catalog, cinco jobs de
Glue, una máquina de estados por pipeline y un workgroup de Athena. Son 29 recursos por
ambiente. El porqué de cada elección está en `docs/adr/`: ADR 0001 para Glue, Step Functions y
Athena, ADR 0003 para gold con dbt.

| Job | Tipo | Qué hace |
| --- | --- | --- |
| `ingest_landing` | Python shell 3.9, 1/16 DPU | Baja los recursos de un dataset a `landing/`. |
| `bronze_load` | Glue 5.0 Spark, N × G.1X | CSV de landing a las tablas Iceberg de bronze. |
| `bronze_reservas` | Python shell 3.9, 1 DPU | Parsea el XLSX anual de reservas y escribe `bronze.reservas` con pyiceberg. |
| `silver_load` | Glue 5.0 Spark, N × G.1X | Aplica un contrato y escribe la tabla silver. |
| `gold_dbt` | Glue 5.0, 2 × G.1X fijos | `dbt build`; el SQL lo ejecuta Athena. |

`ingest_landing`, `bronze_load` y `silver_load` son genéricos: el dataset y el contrato se los
pasa la máquina de estados. `bronze_reservas` recibe el `--dataset` igual pero lo ignora,
porque carga una sola tabla. Cada pipeline es una entrada del mapa `local.pipelines` de
`stepfunctions.tf` con su dataset, sus contratos, qué job usa para bronze y su cron: agregar
uno nuevo son seis líneas. Un pipeline con más de un contrato (producción carga también el
padrón de pozos) genera un estado `silver_<contrato>` por contrato, encadenados.

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

`environment` **no tiene default**: siempre hay que pasar el `-var-file`. Y el workspace tiene
que coincidir con el ambiente del tfvars: `aws_s3_bucket.lakehouse` lleva una `precondition`
que corta el plan si no coinciden, con el comando que hay que correr.

El sufijo llega al código por una sola variable de entorno, `GLUE_DATABASE_SUFFIX`, que
Terraform pasa como argumento a cada job. La consumen los jobs de Spark, el bronze de reservas
y dbt; los YAML de contratos siguen sin nombrar el ambiente
([ADR 0005](../../docs/adr/0005-ambientes-dev-y-prod.md)).

El state vive en S3, un archivo por workspace, con bloqueo en DynamoDB. El bucket y la tabla
los crea [`bootstrap/`](bootstrap/README.md), aplicado el 2026-09-12.

## Desplegar: desde GitHub

Es el camino normal y no requiere ningún comando local. `.github/workflows/deploy.yml` hace:

| Evento | Qué pasa |
| --- | --- |
| Pull request que toca `infra/terraform/**`, `pipelines/**` o `pyproject.toml` | `terraform plan` de dev, para leerlo en el PR. |
| Merge a `main` | `apply` de dev, `uv build` del wheel y subida del wheel y los wrappers a `artifacts/`. |
| Después de dev | `apply` de prod, esperando aprobación manual en el GitHub Environment `prod`. Baja el wheel de dev en vez de reconstruirlo. |

Se autentica con OIDC (`role-to-assume`), sin claves en los secretos del repo. Está habilitado
desde el 2026-09-12, con la variable de repo `DEPLOY_ENABLED = true`. Cómo se llegó ahí, en
[`bootstrap/README.md`](bootstrap/README.md).

## Desplegar: desde una máquina de desarrollo

Solo cuando hace falta aplicar algo sin pasar por `main`. El candado en DynamoDB impide que un
`apply` local y uno del workflow se pisen.

```powershell
cd infra\terraform
terraform init
terraform workspace select -or-create dev      # o prod
terraform plan  -var-file=envs\dev.tfvars
terraform apply -var-file=envs\dev.tfvars
..\..\scripts\aws_deploy.ps1                   # uv build + sube wheel y wrappers a artifacts/
```

`aws_deploy.ps1` (y `aws_deploy.sh`, el mismo script para Git Bash y Linux) lee el bucket y el
ambiente de `terraform output` **del workspace seleccionado**, y lo imprime antes de subir
nada. Los jobs leen su script de `s3://<bucket>/artifacts/` en cada corrida: después de tocar
código alcanza con volver a correrlo, sin `terraform apply`. Si cambia la versión del proyecto,
actualizar también la variable `wheel_name`. El proyecto de dbt (`pipelines/dbt/`) viaja
adentro del wheel, así que un modelo nuevo tampoco necesita Terraform.

## Requisito externo: el DSN de Neon

Un parámetro SecureString por ambiente, con la cadena de conexión al branch de Neon que
corresponda. Se crean a mano y no los maneja Terraform: son secretos.

```powershell
aws ssm put-parameter --name /oil-lakehouse/dev/postgres_dsn --type SecureString `
  --value "postgresql://..." --overwrite
```

## Correr un pipeline

A mano, siempre: el despliegue automático no dispara nada. Los pipelines son
`produccion_pozo_mensual`, `fractura_diaria`, `reservas_mensual` y `gold_mensual`.

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
Los jobs compartidos corren de a uno y el pipeline que llega segundo espera y reintenta
([ADR 0001](../../docs/adr/0001-lakehouse-serverless-en-aws.md)).

Los schedules de EventBridge (uno por máquina) existen pero nacen **deshabilitados** en los dos
ambientes: nada queda corriendo solo y el costo en reposo es cero. Para habilitarlos, cambiar
`enable_schedule` en el tfvars del ambiente y volver a aplicar. Están escalonados para no
pelearse por los jobs compartidos: producción el día 1 a las 6, fractura todos los días a las
7, reservas el día 1 a las 9 y gold el día 1 a las 12, cuando las tres fuentes ya terminaron.

## Consultar en Athena

```powershell
aws athena start-query-execution --work-group oil-lakehouse-prod `
  --query-string "SELECT count(*) FROM silver_prod.pozo_primera_produccion"
aws athena get-query-results --query-execution-id <id>
```

El workgroup fuerza su propia ubicación de resultados (`athena-results/`, que se limpia a los 7
días), así que no hace falta pasar `--result-configuration`. Las tablas de gold no viven ahí:
dbt las escribe en `warehouse/gold/` justamente para que la regla de ciclo de vida no se las
lleve.

## Destruir

A mano, desde una máquina de desarrollo: el workflow nunca destruye.

```powershell
terraform workspace select dev
terraform plan -destroy -var-file=envs\dev.tfvars   # qué se lleva puesto, sin llevárselo
terraform destroy -var-file=envs\dev.tfvars
```

Borra el bucket **con todos los datos adentro** (`force_destroy = true`): landing, las tablas
Iceberg de bronze, silver y gold, los artefactos y los resultados de Athena. También borra las
tres bases del catálogo, los cinco jobs, las máquinas de estados, los schedules, el workgroup y
los tres roles de IAM. No toca el parámetro de SSM ni la base de Neon: el manifiesto de ingesta
sobrevive. Destruir un ambiente no toca al otro: son dos states distintos.

## Reconstruir el entorno de cero

El mismo procedimiento que se corre después de un `destroy` o la primera vez. Toma alrededor de
una hora, casi toda esperando a que la ingesta baje los CSV, y cuesta menos de 2 USD.

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
producción con fractura y con el padrón de pozos. Si alguna fuente falta, el mart sale corto y
los tests de relación entre hechos y dimensiones lo delatan.

Verificación, con `scripts/aws_logs.ps1` para los logs y con Athena para las filas:

```sql
-- Conteos medidos en prod el 2026-09-12. Fractura y producción crecen con cada republicación
-- del portal; reservas y el mart no, porque el ZIP anual y el padrón de pozos ya están
-- cerrados.
SELECT count(*) FROM silver_prod.produccion_pozo;          -- 18.234.202
SELECT count(*) FROM silver_prod.pozo_primera_produccion;  --     86.197
SELECT count(*) FROM silver_prod.fractura;                 --      4.878
SELECT count(*) FROM silver_prod.reservas;                 --    198.734
SELECT count(*) FROM gold_prod.mart_pozo_completacion_produccion;  -- 4.635
```
