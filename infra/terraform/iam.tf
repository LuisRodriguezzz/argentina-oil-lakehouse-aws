# Tres roles, uno por servicio que ejecuta algo: los jobs de Glue, la máquina de estados y
# el scheduler. Cada uno con el permiso mínimo para su tarea.

data "aws_iam_policy_document" "asume" {
  for_each = toset(["glue.amazonaws.com", "states.amazonaws.com", "scheduler.amazonaws.com"])

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = [each.key]
    }
  }
}

# --- rol de los jobs de Glue ------------------------------------------------

resource "aws_iam_role" "glue_job" {
  name               = "${var.project}-glue-job${local.sufijo}"
  description        = "Rol que asumen los jobs de Glue del pipeline (${var.environment})."
  assume_role_policy = data.aws_iam_policy_document.asume["glue.amazonaws.com"].json
}

# Permisos base de Glue (catálogo, CloudWatch Logs, buckets aws-glue-*).
resource "aws_iam_role_policy_attachment" "glue_job_servicio" {
  role       = aws_iam_role.glue_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

locals {
  glue_arn   = "arn:aws:glue:${var.region}:${data.aws_caller_identity.actual.account_id}"
  athena_arn = "arn:aws:athena:${var.region}:${data.aws_caller_identity.actual.account_id}"
  bases_del_catalogo = [
    aws_glue_catalog_database.bronze.name,
    aws_glue_catalog_database.silver.name,
    aws_glue_catalog_database.gold.name,
  ]
}

data "aws_iam_policy_document" "glue_job" {
  statement {
    sid = "LeerYEscribirElLakehouse"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.lakehouse.arn}/*"]
  }

  # El job de gold no ejecuta SQL: se lo manda a Athena, que consulta y escribe con estos
  # permisos. Acotado al workgroup del proyecto, que es el único que el job usa.
  statement {
    sid       = "ConsultarConAthena"
    actions   = ["athena:*"]
    resources = ["${local.athena_arn}:workgroup/${aws_athena_workgroup.lakehouse.name}", "${local.athena_arn}:datacatalog/*"]
  }

  # AWSGlueServiceRole ya trae `glue:*` sobre todo el catálogo, así que esta sentencia hoy no
  # recorta nada: deja escrito qué bases toca el pipeline y sigue valiendo si algún día se
  # reemplaza la política administrada por uno de permiso mínimo.
  statement {
    sid     = "UsarElCatalogoDeLasTresCapas"
    actions = ["glue:*"]
    resources = concat(
      ["${local.glue_arn}:catalog"],
      [for base in local.bases_del_catalogo : "${local.glue_arn}:database/${base}"],
      [for base in local.bases_del_catalogo : "${local.glue_arn}:table/${base}/*"],
    )
  }

  statement {
    sid       = "ListarElBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.lakehouse.arn]
  }

  # Solo los parámetros de su ambiente: el rol de dev no puede leer el DSN del branch de
  # producción de Neon aunque alguien le pase el nombre del parámetro a mano (ADR 0005).
  statement {
    sid       = "LeerElDsnDePostgresDeSuAmbiente"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.actual.account_id}:parameter${local.postgres_dsn_ssm_parameter}"]
  }

  # El SecureString usa la clave por defecto de SSM (alias/aws/ssm), que no se puede nombrar
  # por ARN sin buscarla: se limita por el servicio que hace la llamada.
  statement {
    sid       = "DescifrarConLaClaveDeSsm"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "glue_job" {
  name   = "lakehouse"
  role   = aws_iam_role.glue_job.id
  policy = data.aws_iam_policy_document.glue_job.json
}

# --- rol de la máquina de estados -------------------------------------------

resource "aws_iam_role" "step_functions" {
  name               = "${var.project}-stepfunctions${local.sufijo}"
  description        = "Rol de las máquinas de estados de los pipelines (${var.environment})."
  assume_role_policy = data.aws_iam_policy_document.asume["states.amazonaws.com"].json
}

data "aws_iam_policy_document" "step_functions" {
  # Arrancar el job y mirar cómo va, nada más: la integración `.sync` de Glue espera
  # preguntando con `glue:GetJobRun` (la regla administrada de EventBridge que pide el `.sync`
  # es la de ECS, Batch y EMR, no la de Glue).
  statement {
    sid     = "CorrerLosJobsDeGlue"
    actions = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = [
      aws_glue_job.ingest_landing.arn,
      aws_glue_job.bronze_load.arn,
      aws_glue_job.bronze_reservas.arn,
      aws_glue_job.silver_load.arn,
      aws_glue_job.gold_dbt.arn,
    ]
  }
}

resource "aws_iam_role_policy" "step_functions" {
  name   = "correr-glue"
  role   = aws_iam_role.step_functions.id
  policy = data.aws_iam_policy_document.step_functions.json
}

# --- rol del scheduler ------------------------------------------------------

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler${local.sufijo}"
  description        = "Rol que usa EventBridge Scheduler para arrancar la máquina de estados (${var.environment})."
  assume_role_policy = data.aws_iam_policy_document.asume["scheduler.amazonaws.com"].json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "ArrancarLasMaquinasDeEstados"
    actions   = ["states:StartExecution"]
    resources = [for maquina in aws_sfn_state_machine.pipeline : maquina.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "arrancar-la-maquina"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
