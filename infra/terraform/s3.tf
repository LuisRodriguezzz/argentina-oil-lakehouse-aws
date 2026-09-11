# Un solo bucket para todo el lakehouse, separado por prefijos:
#   landing/         CSV crudos que baja la ingesta
#   warehouse/       tablas Iceberg (bronze y silver)
#   artifacts/       wheel del proyecto y scripts de los jobs de Glue
#   athena-results/  resultados de las consultas (se borran a los 7 días)

data "aws_caller_identity" "actual" {}

locals {
  bucket_name = "oil-lakehouse-${data.aws_caller_identity.actual.account_id}${local.sufijo}"
  bucket_uri  = "s3://${aws_s3_bucket.lakehouse.bucket}"
}

resource "aws_s3_bucket" "lakehouse" {
  bucket = local.bucket_name

  # Entorno efímero: `terraform destroy` tiene que poder borrarlo con datos adentro.
  # Los datos se regeneran corriendo el pipeline; no hay nada que no se pueda rehacer.
  force_destroy = true

  lifecycle {
    # El workspace es lo único que separa los dos states (ADR 0005) y el tfvars es lo único
    # que separa los nombres: aplicar `envs/dev.tfvars` parado en el workspace `prod`
    # escribiría los recursos de dev en el state de prod y dejaría los de prod huérfanos.
    # Se chequea acá, en el primer recurso del que cuelga todo lo demás, y no en un runbook.
    precondition {
      condition     = terraform.workspace == var.environment
      error_message = "El workspace (${terraform.workspace}) no coincide con environment (${var.environment}): correr `terraform workspace select ${var.environment}`."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lakehouse" {
  bucket                  = aws_s3_bucket.lakehouse.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  # Con versionado activado, cada reescritura de una tabla Iceberg deja versiones viejas:
  # sin esta regla el bucket crece para siempre.
  rule {
    id     = "expirar-versiones-viejas"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Los resultados de Athena son descartables: se regeneran corriendo la consulta.
  rule {
    id     = "expirar-resultados-de-athena"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.lakehouse]
}
