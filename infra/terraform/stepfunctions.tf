# Una máquina de estados por pipeline: los pasos en orden, y si uno falla no arranca el
# siguiente. Los jobs de Glue son genéricos y se reutilizan; lo único que cambia entre
# pipelines es el dataset, los contratos, qué job hace bronze y el cron.
#
# `startJobRun.sync` espera a que el job termine y falla si el job falla.
#
# Cada paso mezcla sus argumentos fijos con los que traiga el input de la ejecución (JSONata):
# con input `{}` corre el pipeline completo, y con
#   {"ingesta": {"--only": "^Padr"}}
# se acota la corrida sin tocar la definición. Las claves del input son los nombres de los
# estados: `ingesta`, `bronze` y un `silver_<contrato>` por contrato del pipeline.

locals {
  pipelines = {
    produccion_pozo_mensual = {
      dataset = "produccion_pozo"
      # Dos contratos sobre el mismo dataset: las DDJJ de producción y el padrón de pozos,
      # que bronze separa en dos tablas (pipelines/spark_jobs/bronze_tables.yaml) y que gold
      # necesita como dimensión. Se cargan en este orden, uno después del otro.
      contracts  = ["produccion_pozo", "pozo_primera_produccion"]
      bronze_job = aws_glue_job.bronze_load.name
      # Mensual, el día 1 a las 6: el portal republica el CSV una vez por mes.
      cron = "cron(0 6 1 * ? *)"
    }
    fractura_diaria = {
      dataset    = "fractura"
      contracts  = ["fractura"]
      bronze_job = aws_glue_job.bronze_load.name
      # Diario a las 7: el portal republica el CSV de fractura todos los días.
      cron = "cron(0 7 * * ? *)"
    }
    reservas_mensual = {
      dataset   = "reservas"
      contracts = ["reservas"]
      # El único pipeline cuyo bronze no es Spark: el ZIP anual es un cuadro de Excel y lo
      # parsea un Python shell (glue.tf). El `--dataset` de abajo le llega igual y lo ignora,
      # porque este job carga una sola tabla.
      bronze_job = aws_glue_job.bronze_reservas.name
      # Mensual el día 1 a las 9: la Secretaría publica el ZIP una vez al año, pero mirarlo
      # todos los meses no cuesta nada (el hash decide si hay algo que cargar). Tres horas
      # después de producción, que arranca a las 6 y comparte los jobs de ingesta y silver.
      cron = "cron(0 9 1 * ? *)"
    }
  }

  # Los jobs de Glue corren de a uno y se comparten entre pipelines: si dos máquinas se
  # cruzan, la segunda falla al instante con ConcurrentRunsExceededException en vez de hacer
  # cola. Reintentar cada 5 minutos sin backoff da casi una hora de espera, que alcanza para
  # que termine la corrida que estaba ocupando el job.
  reintentar_si_el_job_esta_ocupado = [{
    ErrorEquals     = ["Glue.ConcurrentRunsExceededException"]
    IntervalSeconds = 300
    MaxAttempts     = 10
    BackoffRate     = 1
  }]

  # Un estado de silver por contrato. El nombre lleva el contrato adentro para poder acotar
  # la corrida a uno solo desde el input de la ejecución.
  estados_silver = { for nombre, pipeline in local.pipelines :
    nombre => [for contrato in pipeline.contracts : "silver_${contrato}"]
  }

  # Argumentos fijos de cada paso, por pipeline. La clave es el nombre del estado.
  fijos = { for nombre, pipeline in local.pipelines : nombre => merge(
    {
      ingesta = { "--dataset" = pipeline.dataset }
      bronze  = { "--dataset" = pipeline.dataset }
    },
    { for contrato in pipeline.contracts : "silver_${contrato}" => { "--contract" = contrato } },
  ) }

  # `$states.context.Execution.Input` y no `$states.input`: el input de un paso es la salida
  # del paso anterior (la corrida de Glue), no el input de la ejecución.
  # El `? :` es obligatorio: una expresión JSONata que no devuelve nada corta la ejecución
  # con QueryEvaluationError en vez de omitir el campo.
  argumentos = { for nombre, pasos in local.fijos : nombre => { for paso, fijos in pasos :
    paso => "{% $merge([${jsonencode(fijos)}, $exists($states.context.Execution.Input.${paso}) ? $states.context.Execution.Input.${paso} : {}]) %}"
  } }

  # Los estados de silver ya armados: cada uno encadena con el contrato siguiente y el
  # último cierra la máquina.
  silver = { for nombre, estados in local.estados_silver : nombre => {
    for i, estado in estados : estado => merge(
      {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Retry    = local.reintentar_si_el_job_esta_ocupado
        Arguments = {
          JobName   = aws_glue_job.silver_load.name
          Arguments = local.argumentos[nombre][estado]
        }
      },
      i == length(estados) - 1 ? { End = true } : { Next = estados[i + 1] },
    )
  } }

  # Gold no es un pipeline de fuente: no ingiere ni tipa nada, corre un solo job que arma los
  # ocho modelos con dbt. Entra igual al mismo `for_each` para no repetir el recurso de la
  # máquina de estados ni el del schedule.
  definiciones = merge(
    { for nombre, pipeline in local.pipelines : nombre => {
      Comment       = "${pipeline.dataset}: landing -> bronze -> silver"
      QueryLanguage = "JSONata"
      StartAt       = "ingesta"
      States = merge(
        {
          ingesta = {
            Type     = "Task"
            Resource = "arn:aws:states:::glue:startJobRun.sync"
            Retry    = local.reintentar_si_el_job_esta_ocupado
            Arguments = {
              JobName   = aws_glue_job.ingest_landing.name
              Arguments = local.argumentos[nombre]["ingesta"]
            }
            Next = "bronze"
          }
          bronze = {
            Type     = "Task"
            Resource = "arn:aws:states:::glue:startJobRun.sync"
            Retry    = local.reintentar_si_el_job_esta_ocupado
            Arguments = {
              JobName   = pipeline.bronze_job
              Arguments = local.argumentos[nombre]["bronze"]
            }
            Next = local.estados_silver[nombre][0]
          }
        },
        local.silver[nombre],
      )
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
            Retry     = local.reintentar_si_el_job_esta_ocupado
            Arguments = { JobName = aws_glue_job.gold_dbt.name }
            End       = true
          }
        }
      }
    },
  )

  # Escalonados a propósito: los cuatro comparten jobs que corren de a uno, así que arrancar
  # todos a la misma hora sería pelearse por ellos. Gold va al mediodía del día 1, cuando los
  # tres pipelines de fuente ya terminaron.
  crons = merge(
    { for nombre, pipeline in local.pipelines : nombre => pipeline.cron },
    { gold_mensual = "cron(0 12 1 * ? *)" },
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
