"""Job de Glue (Spark) que carga la capa silver aplicando un contrato.

Wrapper fino: lee los argumentos de Glue, los exporta a `os.environ` y llama al job.
Silver no toca ni landing ni el manifiesto, así que no necesita el DSN.
"""

from __future__ import annotations

import os
import sys

from awsglue.utils import getResolvedOptions

from pipelines.spark_jobs.silver_load import main as silver_main

# Sin `S3_REGION`: silver no lee landing ni SSM, y el catálogo y S3FileIO resuelven la región
# con el rol del job. Terraform puede seguir pasándolo, getResolvedOptions ignora lo que sobra.
ENV_ARGS = ("GLUE_WAREHOUSE", "GLUE_DATABASE_SUFFIX")


def main() -> int:
    # `--resource-id` es opcional (sirve para reprocesar un recurso suelto) y
    # getResolvedOptions falla si pide un argumento que no vino.
    opcionales = ["resource-id"] if "--resource-id" in sys.argv else []
    args = getResolvedOptions(sys.argv, ["contract", *ENV_ARGS, *opcionales])
    for name in ENV_ARGS:
        os.environ[name] = args[name]
    argv = ["--contract", args["contract"]]
    if args.get("resource_id"):
        argv += ["--resource-id", args["resource_id"]]
    return silver_main(argv)


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0 (el run queda FAILED con "SystemExit: 0"). Solo se corta la
    # ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
