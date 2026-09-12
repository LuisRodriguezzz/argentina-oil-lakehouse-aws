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
    Repo que puede asumir los roles de despliegue, tal como GitHub lo escribe en el claim
    `sub` del token OIDC: `owner@id/repo@id`, con el id numérico de cada uno (desde 2026
    GitHub los incluye; visto en CloudTrail el 2026-09-12). Los ids no cambian aunque el
    repo o la cuenta se renombren: `gh api repos/<owner>/<repo> --jq '.owner.id, .id'`.
    Cualquier otro repo, aunque use el mismo proveedor de GitHub, recibe un AccessDenied.
  EOT
  type        = string
  default     = "LuisRodriguezzz@131310791/argentina-oil-lakehouse-aws@1366578558"
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
