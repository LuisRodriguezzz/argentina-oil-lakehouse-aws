# Dónde vive el state de `infra/terraform/` cuando deje de ser local: un bucket versionado y
# una tabla de locks. Los dos son de los dos ambientes a la vez —con workspaces, dev queda en
# `env:/dev/lakehouse/terraform.tfstate` y prod en `env:/prod/...` del mismo bucket—, y por
# eso viven acá y no adentro de ningún ambiente: un `terraform destroy` de dev no puede
# llevarse el state de prod por delante.

locals {
  state_bucket = coalesce(
    var.state_bucket_name,
    "oil-lakehouse-tfstate-${data.aws_caller_identity.actual.account_id}",
  )
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # Sin `force_destroy`: al revés que el bucket del lakehouse, esto no se regenera corriendo
  # el pipeline. Si alguien lo destruye tiene que vaciarlo a mano y darse cuenta de lo que
  # está haciendo.
  force_destroy = false
}

# Lo que hace que el state remoto sea recuperable: cada `apply` deja una versión nueva y un
# apply que sale mal se revierte volviendo a la anterior.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# El state guarda en claro todo lo que Terraform sabe, incluidos ARNs y cualquier atributo
# sensible que un recurso devuelva. Cifrado siempre.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bloqueo: dos `apply` a la vez sobre el mismo ambiente (la máquina del autor y el workflow
# de GitHub) dejarían el state inconsistente. `LockID` es el nombre de clave que espera el
# backend S3, no se elige.
#
# PAY_PER_REQUEST: son unas pocas escrituras por despliegue. Provisionado costaría más que
# el resto del proyecto junto.
resource "aws_dynamodb_table" "locks" {
  name         = "oil-lakehouse-tfstate-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}
