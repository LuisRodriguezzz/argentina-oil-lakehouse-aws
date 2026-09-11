# Una máquina de estados por pipeline: los pasos en orden, y si uno falla no arranca el
# siguiente. Los jobs de Glue son genéricos y se reutilizan; lo único que cambia entre
# pipelines es el dataset, el contrato, qué job hace bronze y el cron.
#
# `startJobRun.sync` espera a que el job termine y falla si el job falla.
#
# Cada paso mezcla sus argumentos fijos con los que traiga el input de la ejecución (JSONata):
# con input `{}` corre el pipeline completo, y con
#   {"ingesta": {"--only": "^Padr"}, "silver": {"--contract": "pozo_primera_produccion"}}
# se acota la corrida sin tocar la definición.

locals {
  pipelines = {
    produccion_pozo_mensual = {
      dataset    = "produccion_pozo"
      contract   = "produccion_pozo"
      bronze_job = aws_glue_job.bronze_load.name
      # Mensual, el día 1 a las 6: el portal republica el CSV una vez por mes.
      cron = "cron(0 6 1 * ? *)"
    }
    fractura_diaria = {
      dataset    = "fractura"
      contract   = "fractura"
      bronze_job = aws_glue_job.bronze_load.name
      # Diario a las 7: el portal republica el CSV de fractura todos los días.
      cron = "cron(0 7 * * ? *)"
    }
    reservas_mensual = {
      dataset  = "reservas"
      contract = "reservas"
      # El único pipeline cuyo bronze no es Spark: el ZIP anual es un cuadro de Excel y lo
      # parsea un Python shell (glue.tf). El `--dataset` de abajo le llega igual y lo ignora,
      # porque este job carga una sola tabla.
      bronze_job = aws_glue_job.bronze_reservas.name
      # Mensual el día 1 a las 6: la Secretaría publica el ZIP una vez al año, pero mirarlo
      # todos los meses no cuesta nada (el hash decide si hay algo que cargar).
      cron = "cron(0 6 1 * ? *)"
    }
  }

  # Argumentos fijos de cada paso, por pipeline.
  fijos = { for nombre, pipeline in local.pipelines : nombre => {
    ingesta = { "--dataset" = pipeline.dataset }
    bronze  = { "--dataset" = pipeline.dataset }
    silver  = { "--contract" = pipeline.contract }
  } }

  # `$states.context.Execution.Input` y no `$states.input`: el input de un paso es la salida
  # del paso anterior (la corrida de Glue), no el input de la ejecución.
  # El `? :` es obligatorio: una expresión JSONata que no devuelve nada corta la ejecución
  # con QueryEvaluationError en vez de omitir el campo.
  argumentos = { for nombre, pasos in local.fijos : nombre => { for paso, fijos in pasos :
    paso => "{% $merge([${jsonencode(fijos)}, $exists($states.context.Execution.Input.${paso}) ? $states.context.Execution.Input.${paso} : {}]) %}"
  } }

  # Gold no es un pipeline de fuente: no ingiere ni tipa nada, corre un solo job que arma los
  # ocho modelos con dbt. Entra igual al mismo `for_each` para no repetir el recurso de la
  # máquina de estados ni el del schedule.
  definiciones = merge(
    { for nombre, pipeline in local.pipelines : nombre => {
      Comment       = "${pipeline.dataset}: landing -> bronze -> silver"
      QueryLanguage = "JSONata"
      StartAt       = "ingesta"
      States = {
        ingesta = {
          Type     = "Task"
          Resource = "arn:aws:states:::glue:startJobRun.sync"
          Arguments = {
            JobName   = aws_glue_job.ingest_landing.name
            Arguments = local.argumentos[nombre]["ingesta"]
          }
          Next = "bronze"
        }
        bronze = {
          Type     = "Task"
          Resource = "arn:aws:states:::glue:startJobRun.sync"
          Arguments = {
            JobName   = pipeline.bronze_job
            Arguments = local.argumentos[nombre]["bronze"]
          }
          Next = "silver"
        }
        silver = {
          Type     = "Task"
          Resource = "arn:aws:states:::glue:startJobRun.sync"
          Arguments = {
            JobName   = aws_glue_job.silver_load.name
            Arguments = local.argumentos[nombre]["silver"]
          }
          End = true
        }
      }
    } },
    {
      gold_mensual = {
        Comment       = "gold: dbt build sobre silver, con Athena de motor"
        QueryLanguage = "JSONata"
        StartAt       = "gold"
        States = {
          gold = {
            Type      = "Task"
            Resource  = "arn:aws:states:::glue:startJobRun.sync"
            Arguments = { JobName = aws_glue_job.gold_dbt.name }
            End       = true
          }
        }
      }
    },
  )

  # El día 1 a las 6, como los pipelines de fuente.
  crons = merge(
    { for nombre, pipeline in local.pipelines : nombre => pipeline.cron },
    { gold_mensual = "cron(0 6 1 * ? *)" },
  )
}

resource "aws_sfn_state_machine" "pipeline" {
  for_each = local.definiciones

  # Guion bajo, como los nombres de los pipelines.
  name       = "${each.key}${local.sufijo_bajo}"
  role_arn   = aws_iam_role.step_functions.arn
  definition = jsonencode(each.value)
}

# Los schedules nacen deshabilitados en los dos ambientes: el entorno no tiene que quedar
# corriendo solo y el costo en reposo tiene que seguir siendo cero (ADR 0001). Se habilitan
# a propósito cambiando `enable_schedule` en `envs/<ambiente>.tfvars`.
resource "aws_scheduler_schedule" "pipeline" {
  for_each = local.crons

  name                         = "${replace(each.key, "_", "-")}${local.sufijo}"
  state                        = var.enable_schedule ? "ENABLED" : "DISABLED"
  schedule_expression          = each.value
  schedule_expression_timezone = "America/Argentina/Buenos_Aires"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.pipeline[each.key].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})
  }
}
