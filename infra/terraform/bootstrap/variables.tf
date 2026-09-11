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

variable "github_repository" {
  description = <<-EOT
    Repo que puede asumir los roles de despliegue, en formato `owner/repo`. Entra en la
    trust policy del OIDC: cualquier otro repo, aunque use el mismo proveedor de GitHub,
    recibe un AccessDenied al intentar asumirlos.
  EOT
  type        = string
  default     = "LuisRodriguezzz/argentina-oil-lakehouse-aws"
}

variable "github_branch" {
  description = "Rama desde la que se despliega. Un push a cualquier otra no puede asumir el rol de dev."
  type        = string
  default     = "main"
}

variable "state_bucket_name" {
  description = <<-EOT
    Bucket del state remoto. Con el id de la cuenta adentro porque los nombres de S3 son
    globales. Vacío usa `oil-lakehouse-tfstate-<id de la cuenta>`.
  EOT
  type        = string
  default     = ""
}
