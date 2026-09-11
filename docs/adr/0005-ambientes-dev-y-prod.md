# ADR 0005 — Dos ambientes, dev y prod, en un solo repo

**Estado:** aceptada · 2026-09-06 · **todavía no aplicada contra AWS**

## Contexto

Un solo ambiente alcanza mientras el entorno es efímero (ADR 0001) y lo toca una sola persona:
un `terraform.tfstate` local, nombres sin sufijo y despliegues a mano.

No alcanza para dos cosas que sí importan en un proyecto de portfolio de ingeniería de datos:
probar un cambio de infraestructura sin arriesgar el entorno del que salen los números que cita
el README, y mostrar cómo se separa un ambiente de otro cuando el presupuesto no da para dos
cuentas de AWS.

## Decisión

### Un repo, un directorio de Terraform, una variable

`var.environment` (`dev` o `prod`, validada, **sin valor por defecto**) sufija el nombre de
todos los recursos. Sin default a propósito: obliga a pasar `-var-file=envs/dev.tfvars` o
`envs/prod.tfvars` y nadie aplica un ambiente por descuido.

No hay módulo, ni directorio por ambiente, ni plantillas: son los mismos `.tf` de siempre con
`local.sufijo` pegado a cada `name`. La diferencia entre los dos ambientes cabe en dos archivos
de tres líneas (`envs/dev.tfvars`, `envs/prod.tfvars`): el nombre del ambiente, la cantidad de
workers de Spark (2 en dev, 4 en prod) y `enable_schedule`, que es `false` en los dos.

### Sufijo, con dos separadores

- **Guion** en lo que ya se nombraba con guiones: bucket (`oil-lakehouse-<cuenta>-dev`), roles
  (`argentina-oil-lakehouse-glue-job-dev`), workgroup (`oil-lakehouse-dev`) y schedules
  (`fractura-diaria-dev`).
- **Guion bajo** en lo que ya se nombraba con guion bajo: jobs de Glue (`bronze_load_dev`),
  máquinas de estados (`fractura_diaria_dev`) y las tres bases del catálogo (`bronze_dev`,
  `silver_dev`, `gold_dev`).

Mezclar uno solo daba nombres que se leen mal (`ingest_landing-dev`). En las bases pesa además
que son identificadores de SQL: `SELECT ... FROM silver_prod.produccion_pozo` se escribe sin
comillas, y con guion Athena obligaría a `"silver-prod"`.

Sufijo y no prefijo: en la consola de Glue y en `SHOW DATABASES` las bases quedan ordenadas por
capa, que es como se las busca.

### Cómo llega el sufijo al código: una sola variable de entorno

Los YAML del proyecto (contratos y `bronze_tables.yaml`) siguen diciendo `lake.silver.fractura`,
sin ambiente. Terraform pasa `--GLUE_DATABASE_SUFFIX` a cada job y:

- Los jobs de Spark lo leen en `LakehouseConfig` y lo aplican con
  `pipelines.spark_jobs.bronze_rules.with_suffix`, que se llama en los dos únicos lugares donde
  un nombre de tabla sale de un YAML: `load_table_rules` y `load_contract`. Todo lo demás
  (cuarentena `_rejects`, historial `dq_runs`) se deriva de ahí y hereda el sufijo.
- dbt lo compone en Jinja: `schema: "gold{{ env_var('GLUE_DATABASE_SUFFIX', '') }}"` en
  `profiles.yml` y lo mismo para silver en `models/sources.yml`.

La alternativa era escribir el ambiente en cada YAML de contrato y en cada `source` de dbt. Son
4 contratos, 1 mapeo de bronze y 11 tablas de fuentes, todos con el mismo sufijo: un lugar donde
equivocarse por cada archivo, y contratos que dejarían de ser legibles solos.

### Aislamiento del state: workspaces, no carpetas

Un solo directorio y un workspace por ambiente: `terraform workspace select dev`. Con backend
local queda `terraform.tfstate.d/dev/` y `terraform.tfstate.d/prod/`; con el backend S3 del día
que se aplique `bootstrap/`, queda `env:/dev/...` y `env:/prod/...` del mismo bucket, sin tocar
el bloque de backend.

Carpetas por ambiente (`envs/dev/`, `envs/prod/`, cada una con su backend) es la alternativa que
más se ve, y tiene una ventaja real: el ambiente es visible en el path y es imposible aplicar el
equivocado. Pero para no duplicar los `.tf` obliga a extraer un módulo `modules/lakehouse/` y a
que cada carpeta sea un `module` con veinte variables de paso. Eso es exactamente la indirección
que el resto del repo evita, y por un solo eje de variación (dev/prod) con tres parámetros de
diferencia.

El riesgo de los workspaces —aplicar `dev.tfvars` parado en el workspace `prod`— se tapa en
código y no en un runbook: `aws_s3_bucket.lakehouse` tiene una `precondition` que exige
`terraform.workspace == var.environment` y corta el plan con el comando que hay que correr.

### Autenticación de CI: OIDC, cero secretos

`.github/workflows/deploy.yml` asume un rol por ambiente con
`aws-actions/configure-aws-credentials` y `role-to-assume`. No hay `AWS_ACCESS_KEY_ID` en los
secretos del repo: nada que rotar y nada que se filtre. El proveedor OIDC y los dos roles están
definidos en `infra/terraform/bootstrap/oidc.tf`.

La trust policy de **dev** acepta `repo:<owner>/<repo>:ref:refs/heads/main` y
`repo:<owner>/<repo>:pull_request` (el segundo, porque el `plan` de cada PR necesita leer la
cuenta). La de **prod** acepta un solo claim: `repo:<owner>/<repo>:environment:prod`, que GitHub
emite únicamente cuando el job declara `environment: prod`. Como ese environment está
configurado con aprobación manual y con "deployment branches: main only", el rol de prod queda
atado a la puerta que hay que abrir a mano, que es más ajustado que mirar la rama.

### Aprobación manual en prod, mismo artefacto en los dos

Un push a `main` aplica dev solo y sube el wheel; el job de prod depende de él, declara
`environment: prod` y queda esperando a que una persona lo apruebe. El wheel **no se vuelve a
construir**: se baja el artefacto que produjo el job de dev, así lo que corre en prod es
literalmente el mismo archivo que ya corrió en dev.

El gate de "solo se despliega lo que pasó CI" es la protección de rama: a `main` se llega por
pull request con el workflow `ci` como check obligatorio (ADR 0004). Repetir los tests en
`deploy.yml` sería correrlos dos veces por el mismo commit.

### Neon y SSM: un branch y un parámetro por ambiente

El manifiesto de ingesta vive en Neon (ADR 0001). Neon tiene branches copy-on-write: `main` es
el de producción y `dev` sale de él, arranca con los mismos datos y cuesta lo mismo (cero, en el
plan gratuito). Cada uno tiene su cadena de conexión, guardada como SecureString en
`/oil-lakehouse/dev/postgres_dsn` y `/oil-lakehouse/prod/postgres_dsn`. Los parámetros se crean
a mano, fuera de Terraform: son secretos.

La política del rol de Glue quedó acotada al parámetro de **su** ambiente y no a
`/oil-lakehouse/*`: una corrida de dev no puede leer el DSN de producción ni pasándole el nombre
del parámetro a mano.

### Dos ambientes y no tres

No hay `staging`. Un tercer ambiente se justifica cuando hay algo que probar que dev no puede:
datos de volumen real, integraciones con terceros, o varios equipos que necesitan una cola de
release. Acá dev y prod leen las mismas fuentes públicas y el que despliega es una persona:
staging sería un tercer bucket con los mismos datos, un tercer state que mantener y un paso más
de aprobación que nadie mira. Cuando prod se rompa por algo que dev no vio, entonces sí.

## Consecuencias

- Los dos ambientes conviven en la misma cuenta sin pisarse, y el costo en reposo sigue siendo
  cero: los schedules nacen deshabilitados en los dos (`enable_schedule = false`).
- **Nada de esto está aplicado en este repo: no hay ningún ambiente desplegado.** Son 29
  recursos por ambiente escritos y validados (`terraform validate`, `terraform fmt`), no
  infraestructura corriendo. El state sigue siendo local, `bootstrap/` nunca corrió y
  `deploy.yml` está deshabilitado (`if: vars.DEPLOY_ENABLED == 'true'`, variable que no existe).
  El README raíz lo dice en "Qué se verificó y qué no".
- El workflow no puede aplicar hasta que el state sea remoto: un runner de GitHub arranca vacío
  y con backend local creería que no existe nada. Aplicar `bootstrap/` es el primer paso de
  habilitarlo, no un extra.
- Aparece una variable de entorno más en el contrato entre Terraform y el código
  (`GLUE_DATABASE_SUFFIX`). Si Terraform deja de pasarla, los jobs escriben en `bronze` a secas
  en vez de fallar: es un default silencioso, y el precio de que los contratos no nombren el
  ambiente.
