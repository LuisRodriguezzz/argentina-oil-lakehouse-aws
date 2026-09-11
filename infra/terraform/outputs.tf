output "environment" {
  description = "Ambiente de este state. Se imprime para poder chequearlo antes de tocar nada."
  value       = var.environment
}

output "lakehouse_bucket" {
  description = "Bucket del lakehouse. Lo lee scripts/aws_deploy.ps1 para subir los artefactos."
  value       = aws_s3_bucket.lakehouse.bucket
}

output "glue_jobs" {
  description = "Nombres de los jobs de Glue, en el orden en que corren."
  value = [
    aws_glue_job.ingest_landing.name,
    aws_glue_job.bronze_load.name,
    aws_glue_job.bronze_reservas.name,
    aws_glue_job.silver_load.name,
    aws_glue_job.gold_dbt.name,
  ]
}

output "state_machine_arns" {
  description = <<-EOT
    ARN de la máquina de estados de cada pipeline. La clave es el nombre del pipeline sin
    sufijo (`fractura_diaria`) y no el de la máquina (`fractura_diaria_dev`), así los
    comandos del README valen igual en los dos ambientes.
  EOT
  value       = { for nombre, maquina in aws_sfn_state_machine.pipeline : nombre => maquina.arn }
}

output "glue_databases" {
  description = "Bases del catálogo de este ambiente, para consultar en Athena."
  value = [
    aws_glue_catalog_database.bronze.name,
    aws_glue_catalog_database.silver.name,
    aws_glue_catalog_database.gold.name,
  ]
}

output "postgres_dsn_ssm_parameter" {
  description = "Parámetro SecureString con el DSN de Neon de este ambiente. Se crea a mano: es un secreto."
  value       = local.postgres_dsn_ssm_parameter
}

output "athena_workgroup" {
  description = "Workgroup de Athena para consultar bronze y silver."
  value       = aws_athena_workgroup.lakehouse.name
}
