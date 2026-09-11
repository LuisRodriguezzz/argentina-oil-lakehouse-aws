#!/usr/bin/env bash
# Sube los artefactos que ejecutan los jobs de Glue: el wheel del proyecto y los wrappers
# de pipelines/aws/. La infraestructura la crea Terraform; esto solo publica codigo.
# Uso: scripts/aws_deploy.sh
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_dir="$repo/infra/terraform"

# En Git Bash la CLI de AWS puede no estar en el PATH.
aws_cli="$(command -v aws || echo "/c/Program Files/Amazon/AWSCLIV2/aws.exe")"

echo "== uv build =="
uv build --wheel --project "$repo"
wheel="$(ls -t "$repo"/dist/*.whl | head -1)"

# Los outputs salen del workspace seleccionado: se publica en el ambiente en el que uno
# este parado. Se imprime antes de subir nada, que es barato y evita subir a prod creyendo
# que se sube a dev (ADR 0005).
ambiente="$(terraform -chdir="$terraform_dir" output -raw environment)"
bucket="$(terraform -chdir="$terraform_dir" output -raw lakehouse_bucket)"
destino="s3://$bucket/artifacts"

echo "== ambiente $ambiente: subiendo a $destino =="
"$aws_cli" s3 cp "$wheel" "$destino/$(basename "$wheel")"
# Todos los *_job.py de golpe: un job nuevo no necesita tocar este script.
for script in "$repo"/pipelines/aws/*_job.py; do
  "$aws_cli" s3 cp "$script" "$destino/$(basename "$script")"
done

echo
echo "wheel:   $destino/$(basename "$wheel")"
echo "scripts: $destino/$(cd "$repo/pipelines/aws" && echo *_job.py | tr ' ' ',')"
echo "Si el nombre del wheel cambio, actualiza la variable wheel_name de Terraform."
