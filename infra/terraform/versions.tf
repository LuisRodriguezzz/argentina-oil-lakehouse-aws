terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State remoto en el bucket que crea `infra/terraform/bootstrap/` (aplicado el 2026-09-12):
  # un runner de GitHub arranca vacío y sin esto no sabría qué existe (ADR 0005). Con
  # workspaces, el backend S3 guarda cada ambiente en `env:/<workspace>/<key>` solo, así que
  # un único bloque sirve para dev y prod. La tabla de DynamoDB evita que la máquina del
  # autor y el workflow apliquen a la vez.
  backend "s3" {
    bucket         = "oil-lakehouse-tfstate-180111006749"
    key            = "lakehouse/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "oil-lakehouse-tfstate-locks"
  }
  #
  # El lock con tabla de DynamoDB es el que entiende cualquier versión de Terraform. Desde
  # la 1.11 hay una alternativa sin tabla, `use_lockfile = true`, que deja el lock como un
  # objeto `.tflock` en el mismo bucket; si se usa esa, la tabla de bootstrap sobra.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
    }
  }
}

locals {
  # Dos formas del mismo sufijo, porque los nombres de AWS no usan todos el mismo separador
  # y mezclarlos se lee mal (`ingest_landing-dev`):
  #   - guion para lo que ya se nombra con guiones: bucket, roles, workgroup, schedules.
  #   - guion bajo para lo que ya se nombra con guion bajo y además se escribe en SQL: jobs
  #     de Glue, máquinas de estados y las tres bases del catálogo (`silver_dev.fractura`).
  sufijo      = "-${var.environment}"
  sufijo_bajo = "_${var.environment}"

  # Un parámetro de SSM por ambiente: dev apunta al branch `dev` de Neon y prod al `main`,
  # así una corrida de dev no puede escribir el manifiesto de producción (ADR 0005). Se
  # crean a mano, fuera de Terraform: son secretos.
  postgres_dsn_ssm_parameter = "/oil-lakehouse/${var.environment}/postgres_dsn"
}
