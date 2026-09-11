# Muestra, para la última corrida de cada job de Glue, las líneas de log que importan.
# Evita navegar CloudWatch a mano. Uso: scripts/aws_logs.ps1 [-Ambiente prod] [-Todo]
#   -Ambiente  sufijo de los jobs; cada ambiente tiene los suyos (`bronze_load_dev`).
#   -Todo      imprime las últimas 40 líneas de cada job en vez de solo el resumen.
param([string]$Ambiente = "dev", [switch]$Todo)

$ErrorActionPreference = "Continue"
$aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $aws)) { $aws = "aws" }

# Los jobs de Spark escriben en /aws-glue/jobs/output. El Python shell manda stdout a
# /aws-glue/python-jobs/output y stderr (donde escribe `logging`) a /aws-glue/python-jobs/error.
$jobs = @(
    @{ name = "ingest_landing"; group = "/aws-glue/python-jobs/error" },
    @{ name = "bronze_load"; group = "/aws-glue/jobs/output" },
    @{ name = "bronze_reservas"; group = "/aws-glue/python-jobs/error" },
    @{ name = "silver_load"; group = "/aws-glue/jobs/output" },
    @{ name = "gold_dbt"; group = "/aws-glue/jobs/output" }
)
# Palabras que identifican las líneas de nuestro programa (no las de Spark ni de pip).
# `OK=` y `PASS=` son el resumen final de dbt; `Completed` marca cada modelo terminado.
$filtro = '?pendientes ?resumen ?unchanged ?cargado ?descargando ?"ok=" ?rechazadas ?ERROR ?"PASS=" ?Completed'

foreach ($j in $jobs) {
    $nombre = "$($j.name)_$Ambiente"
    $run = (& $aws glue get-job-runs --job-name $nombre --max-results 1 `
            --query "JobRuns[0].{id:Id,state:JobRunState,secs:ExecutionTime,started:StartedOn}" --output json) | ConvertFrom-Json
    if (-not $run) { "=== ${nombre}: sin corridas"; continue }
    "=== $nombre | $($run.state) | $($run.secs) s | inicio $($run.started)"
    if ($Todo) {
        $lineas = (& $aws logs get-log-events --log-group-name $j.group --log-stream-name $run.id `
                --query "events[-40:].message" --output text)
    } else {
        $lineas = (& $aws logs filter-log-events --log-group-name $j.group --log-stream-name-prefix $run.id `
                --filter-pattern $filtro --query "events[].message" --output text)
    }
    $lineas = ($lineas -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    if ($lineas) { $lineas | ForEach-Object { "   $_" } } else { "   (sin líneas de nuestro programa; probá -Todo)" }
}
