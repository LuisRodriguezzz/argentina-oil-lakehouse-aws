variable "environment" {
  description = <<-EOT
    Ambiente que se despliega: `dev` o `prod`. Sufija el nombre de todos los recursos, así
    los dos conviven en la misma cuenta sin pisarse (ADR 0005). No tiene default a
    propósito: se pasa siempre con `-var-file=envs/dev.tfvars` o `envs/prod.tfvars`, para
    que nadie aplique un ambiente por descuido.
  EOT
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment tiene que ser dev o prod: son los dos ambientes del ADR 0005."
  }
}

variable "project" {
  description = "Nombre del proyecto; se aplica como tag a todos los recursos."
  type        = string
  default     = "argentina-oil-lakehouse"
}

variable "region" {
  description = "Región de AWS."
  type        = string
  default     = "us-east-1"
}

variable "wheel_name" {
  description = "Nombre del wheel que `scripts/aws_deploy.ps1` sube a artifacts/ (viene de la versión en pyproject.toml)."
  type        = string
  default     = "argentina_oil_lakehouse_aws-0.1.0-py3-none-any.whl"
}

variable "number_of_workers" {
  description = <<-EOT
    Workers G.1X de los jobs de Spark (bronze, silver, gold). Dos en dev, que es lo mínimo
    de Glue y alcanza para probar que el pipeline corre; cuatro en prod, donde el CSV de
    producción son 18 millones de filas.
  EOT
  type        = number
  default     = 2

  validation {
    condition     = var.number_of_workers >= 2
    error_message = "Glue exige al menos 2 workers en un job de tipo glueetl."
  }
}

variable "enable_schedule" {
  description = "Deja los schedules mensuales habilitados. Falso por defecto: nada corriendo si nadie lo pide."
  type        = bool
  default     = false
}
