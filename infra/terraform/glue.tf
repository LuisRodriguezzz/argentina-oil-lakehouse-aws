# Catálogo y jobs. El catálogo Spark se llama `lake`: `lake.bronze.x` se resuelve contra la
# base `bronze` del Glue Data Catalog.

# Las tres bases llevan el sufijo del ambiente con guion bajo (`silver_dev`, `silver_prod`)
# porque además de nombres de AWS son identificadores de SQL: en Athena se escribe
# `SELECT ... FROM silver_dev.produccion_pozo` sin comillas. El código no las nombra: los
# YAML de contratos siguen diciendo `lake.silver.fractura` y los jobs le pegan el sufijo al
# namespace leyendo `GLUE_DATABASE_SUFFIX` (ADR 0005).

resource "aws_glue_catalog_database" "bronze" {
  name        = "bronze${local.sufijo_bajo}"
  description = "Capa bronze: CSV de landing copiados a Iceberg sin tipar (${var.environment})."
}

resource "aws_glue_catalog_database" "silver" {
  name        = "silver${local.sufijo_bajo}"
  description = "Capa silver: tablas tipadas según los contratos de pipelines/contracts (${var.environment})."
}

resource "aws_glue_catalog_database" "gold" {
  name        = "gold${local.sufijo_bajo}"
  description = "Capa gold: modelo dimensional construido con dbt sobre Athena, ADR 0003 (${var.environment})."
}

locals {
  artifacts_uri = "${local.bucket_uri}/artifacts"

  # Configuración común a todos los jobs: llega como argumentos por defecto y los wrappers
  # de pipelines/aws la exportan a os.environ, que es de donde la lee el código.
  common_arguments = {
    "--GLUE_WAREHOUSE"    = "${local.bucket_uri}/warehouse"
    "--S3_LANDING_BUCKET" = aws_s3_bucket.lakehouse.bucket
    "--S3_LANDING_PREFIX" = "landing"
    "--S3_REGION"         = var.region
    # Sufijo del ambiente para las bases del catálogo: `lake.bronze` -> `lake.bronze_dev`.
    # Lo aplican `pipelines/spark_jobs/bronze_rules.with_suffix` y, en dbt, el `env_var`
    # de `models/sources.yml` y `profiles.yml`.
    "--GLUE_DATABASE_SUFFIX" = local.sufijo_bajo
  }

  # Los jars de Iceberg los pone Glue con --datalake-formats: no hay spark.jars.packages.
  # El wheel se instala con pip (--additional-python-modules) y no se pasa por
  # --extra-py-files porque los .yaml de contratos se leen con Path().read_text() y desde
  # un wheel en sys.path (zipimport) no son archivos reales. --no-deps evita traer pyiceberg,
  # pyarrow y el resto de dependencias del wheel, que los jobs de Spark no usan.
  spark_arguments = merge(local.common_arguments, {
    "--datalake-formats"                = "iceberg"
    "--additional-python-modules"       = "${local.artifacts_uri}/${var.wheel_name},pyyaml==6.0.3"
    "--python-modules-installer-option" = "--no-deps"
    # Job insights escribe métricas y logs extra en CloudWatch que no vamos a mirar.
    "--enable-job-insights" = "false"
  })
}

# Los jobs de ingesta, bronze y silver son genéricos: el dataset y el contrato no van en los
# argumentos por defecto, los pasa la máquina de estados de cada pipeline (stepfunctions.tf).

# Ingesta: baja los CSV del portal a landing/. Es I/O de red, no necesita Spark, así que va
# como Python shell con 1/16 de DPU (lo más barato que ofrece Glue).
resource "aws_glue_job" "ingest_landing" {
  name         = "ingest_landing${local.sufijo_bajo}"
  description  = "Descarga los recursos de un dataset a landing/ y registra el manifiesto."
  role_arn     = aws_iam_role.glue_job.arn
  max_capacity = 0.0625
  max_retries  = 0
  timeout      = 60

  command {
    name            = "pythonshell"
    python_version  = "3.9"
    script_location = "${local.artifacts_uri}/ingest_job.py"
  }

  default_arguments = merge(local.common_arguments, {
    # Python shell no acepta wheels de S3 en --additional-python-modules, y lo que llega
    # por --extra-py-files lo instala con pip, que rechaza este wheel porque el proyecto
    # pide Python >= 3.11. El script se baja el wheel de acá y lo descomprime a mano.
    "--WHEEL_S3_URI" = "${local.artifacts_uri}/${var.wheel_name}"
    # Versiones mas viejas que las de uv.lock a proposito: Python shell trae 3.9 y
    # psycopg >= 3.3 y pydantic-settings >= 2.10 piden 3.10. Es la unica parte del
    # proyecto que corre en 3.9, y solo usa API estable de esas dos librerias.
    "--additional-python-modules" = "sqlalchemy==2.0.52,psycopg[binary]==3.2.13,pydantic-settings==2.9.1,pyyaml==6.0.3"
    "--library-set"               = "analytics"
    # El DSN es secreto: viaja por SSM, nunca en los argumentos del job.
    "--POSTGRES_DSN_SSM_PARAMETER" = local.postgres_dsn_ssm_parameter
  })
}

resource "aws_glue_job" "bronze_load" {
  name              = "bronze_load${local.sufijo_bajo}"
  description       = "Copia los CSV de landing a las tablas Iceberg de bronze."
  role_arn          = aws_iam_role.glue_job.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = var.number_of_workers
  max_retries       = 0
  timeout           = 60

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "${local.artifacts_uri}/bronze_job.py"
  }

  default_arguments = merge(local.spark_arguments, {
    "--POSTGRES_DSN_SSM_PARAMETER" = local.postgres_dsn_ssm_parameter
  })
}

# Bronze de reservas: el ZIP anual son 400 KB y el trabajo es desarmar un cuadro de Excel,
# algo que Spark no lee. Va como Python shell y escribe la tabla Iceberg con pyiceberg contra
# el Glue Data Catalog (pipelines/reservas/bronze_load.py).
resource "aws_glue_job" "bronze_reservas" {
  name        = "bronze_reservas${local.sufijo_bajo}"
  description = "Parsea el XLSX anual de reservas de landing/ y escribe bronze.reservas."
  role_arn    = aws_iam_role.glue_job.arn
  # 1 DPU (16 GB) y no 1/16: openpyxl levanta la planilla entera en memoria y pyarrow arma
  # las 200.000 filas antes de escribirlas. En 1 GB no entra.
  max_capacity = 1
  max_retries  = 0
  timeout      = 60

  command {
    name            = "pythonshell"
    python_version  = "3.9"
    script_location = "${local.artifacts_uri}/bronze_reservas_job.py"
  }

  default_arguments = merge(local.common_arguments, {
    # Mismo truco que ingest_landing: el script se baja el wheel y lo descomprime a mano.
    "--WHEEL_S3_URI" = "${local.artifacts_uri}/${var.wheel_name}"
    # Versiones mas viejas que las de uv.lock a proposito: Python shell trae 3.9 y pyiceberg
    # >= 0.11 y psycopg >= 3.3 piden 3.10. pyarrow va aparte y no como extra de pyiceberg:
    # Glue parte esta lista por comas, asi que un `[glue,pyarrow]` se rompe al medio. Es el
    # FileIO con el que pyiceberg escribe los Parquet, y resuelve las credenciales del rol solo.
    # pydantic-settings no lo usa este job, pero `pipelines.ingest.__init__` reexporta
    # `Settings` y basta con importar el manifiesto para que haga falta.
    "--additional-python-modules"  = "pyiceberg[glue]==0.10.0,pyarrow==17.0.0,openpyxl==3.1.5,sqlalchemy==2.0.52,psycopg[binary]==3.2.13,pydantic-settings==2.9.1"
    "--POSTGRES_DSN_SSM_PARAMETER" = local.postgres_dsn_ssm_parameter
  })
}

resource "aws_glue_job" "silver_load" {
  name              = "silver_load${local.sufijo_bajo}"
  description       = "Aplica un contrato de datos sobre bronze y escribe la tabla silver."
  role_arn          = aws_iam_role.glue_job.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = var.number_of_workers
  max_retries       = 0
  timeout           = 60

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "${local.artifacts_uri}/silver_job.py"
  }

  default_arguments = local.spark_arguments
}

# Gold: `dbt build` contra Athena (ADR 0003). No usa Spark —el trabajo lo hace Athena— pero
# corre igual sobre Glue 5.0 y no sobre Python shell porque Python shell sigue clavado en
# Python 3.9 y dbt-core lo dejó de soportar en la 1.11.
resource "aws_glue_job" "gold_dbt" {
  name              = "gold_dbt${local.sufijo_bajo}"
  description       = "Construye el modelo dimensional de gold corriendo dbt sobre Athena."
  role_arn          = aws_iam_role.glue_job.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = var.number_of_workers
  max_retries       = 0
  timeout           = 60

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "${local.artifacts_uri}/gold_dbt_job.py"
  }

  default_arguments = {
    # Acá el wheel se instala con dependencias (sin --no-deps, al revés que los jobs de
    # Spark): pip tiene que resolver las de dbt. El wheel viene por el proyecto de dbt, que
    # viaja adentro (pipelines/dbt/) y queda en disco como archivos de verdad.
    "--additional-python-modules" = "${local.artifacts_uri}/${var.wheel_name},dbt-core==1.11.14,dbt-athena==1.11.0"
    "--enable-job-insights"       = "false"

    # Lo que lee el target `aws` de profiles.yml, vía os.environ.
    "--AWS_REGION"       = var.region
    "--ATHENA_WORKGROUP" = aws_athena_workgroup.lakehouse.name
    "--ATHENA_DATABASE"  = "awsdatacatalog"
    # `schema: "gold{{ env_var('GLUE_DATABASE_SUFFIX', '') }}"` en profiles.yml y lo mismo
    # para las fuentes de silver en models/sources.yml: un solo sufijo para todo el proyecto.
    "--GLUE_DATABASE_SUFFIX" = local.sufijo_bajo
    "--S3_STAGING_DIR"       = "${local.bucket_uri}/athena-results/"
    # Los datos de las tablas van al warehouse y no debajo de athena-results/, que la regla
    # de ciclo de vida del bucket vacía a los 7 días.
    "--S3_DATA_DIR" = "${local.bucket_uri}/warehouse/gold/"
  }
}
