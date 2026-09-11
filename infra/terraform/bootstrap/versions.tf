terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # El state del bootstrap es local y se queda local: es el huevo y la gallina. Este
  # directorio crea el bucket y la tabla donde vive el state del lakehouse, así que no puede
  # guardarse ahí. Son diez recursos que casi nunca cambian; si se pierde el archivo se
  # reimportan con `terraform import` o se leen de la consola.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project = var.project
      # Sin `environment`: estos recursos son de los dos ambientes a la vez.
      scope = "bootstrap"
    }
  }
}

data "aws_caller_identity" "actual" {}
