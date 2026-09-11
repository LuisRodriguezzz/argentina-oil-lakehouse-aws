# Athena es el motor de consulta del lakehouse (ADR 0001). Lee las tablas Iceberg que
# escriben los jobs a través del Glue Data Catalog: no hay que declarar nada acá.

resource "aws_athena_workgroup" "lakehouse" {
  # Uno por ambiente: el workgroup fija dónde caen los resultados, y los de dev no tienen
  # por qué escribir en el bucket de prod.
  name        = "oil-lakehouse${local.sufijo}"
  description = "Consultas sobre bronze y silver del lakehouse (${var.environment})."

  # El bucket se borra con el entorno; el workgroup no puede quedar apuntando a la nada.
  force_destroy = true

  configuration {
    # Nadie puede mandar sus resultados a otro lado ni saltearse esta configuración.
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false

    result_configuration {
      output_location = "${local.bucket_uri}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
