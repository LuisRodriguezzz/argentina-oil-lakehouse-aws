output "state_bucket" {
  description = "Bucket del state remoto. Es el `bucket` del backend S3 comentado en ../versions.tf."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "Tabla de locks. Es el `dynamodb_table` del backend S3 comentado en ../versions.tf."
  value       = aws_dynamodb_table.locks.name
}

output "github_role_arns" {
  description = <<-EOT
    ARN del rol por ambiente. Van en las variables `AWS_ROLE_DEV` y `AWS_ROLE_PROD` del repo
    (Settings > Secrets and variables > Actions > Variables), que es lo que lee
    `.github/workflows/deploy.yml` en `role-to-assume`. Son ARNs, no secretos.
  EOT
  value       = { for ambiente, rol in aws_iam_role.github : ambiente => rol.arn }
}
