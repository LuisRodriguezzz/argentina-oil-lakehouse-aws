# Sube los artefactos que ejecutan los jobs de Glue: el wheel del proyecto y los wrappers
# de pipelines/aws/. La infraestructura la crea Terraform; esto solo publica codigo.
# Uso: scripts\aws_deploy.ps1

# `Continue` y no `Stop`: uv y aws escriben progreso en stderr y con `Stop` PowerShell lo
# toma como error. El control se hace mirando el codigo de salida de cada comando.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$terraform = Join-Path $repo "infra\terraform"

function Invoke-O-Fallar {
  param([string]$Que)
  if ($LASTEXITCODE -ne 0) { throw "$Que fallo con codigo $LASTEXITCODE" }
}

# La CLI de AWS no siempre esta en el PATH.
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }

Write-Host "== uv build =="
uv build --wheel --project $repo
Invoke-O-Fallar "uv build"
$wheel = Get-ChildItem (Join-Path $repo "dist\*.whl") | Sort-Object LastWriteTime | Select-Object -Last 1

# Terraform instalado con winget puede no estar en el PATH de una sesion recien abierta.
$tf = (Get-Command terraform -ErrorAction SilentlyContinue).Source
if (-not $tf) {
  $tf = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter terraform.exe -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
# Los outputs salen del workspace seleccionado: se publica en el ambiente en el que uno
# este parado. Se imprime antes de subir nada, que es barato y evita subir a prod creyendo
# que se sube a dev (ADR 0005).
$ambiente = & $tf -chdir="$terraform" output -raw environment
Invoke-O-Fallar "terraform output environment"
$bucket = & $tf -chdir="$terraform" output -raw lakehouse_bucket
Invoke-O-Fallar "terraform output"
$destino = "s3://$bucket/artifacts"

Write-Host "== ambiente $ambiente: subiendo a $destino =="
& $aws s3 cp $wheel.FullName "$destino/$($wheel.Name)"
Invoke-O-Fallar "aws s3 cp del wheel"
# Todos los *_job.py de golpe: un job nuevo no necesita tocar este script.
$wrappers = Get-ChildItem (Join-Path $repo "pipelines\aws\*_job.py")
foreach ($script in $wrappers) {
  & $aws s3 cp $script.FullName "$destino/$($script.Name)"
  Invoke-O-Fallar "aws s3 cp de $($script.Name)"
}

Write-Host ""
Write-Host "wheel:   $destino/$($wheel.Name)"
Write-Host "scripts: $destino/{$($wrappers.Name -join ',')}"
Write-Host "Si el nombre del wheel cambio, actualiza la variable wheel_name de Terraform."
