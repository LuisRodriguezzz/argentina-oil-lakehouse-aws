"""Job de Glue (Spark) que carga la capa bronze.

Wrapper fino: lee los argumentos de Glue, los exporta a `os.environ` (que es de donde
los lee `pipelines.spark_jobs.config`) y llama al job.
"""

from __future__ import annotations

import os
import sys

from awsglue.utils import getResolvedOptions

from pipelines.aws.ssm import parameter_value
from pipelines.spark_jobs.bronze_load import main as bronze_main

ENV_ARGS = (
    "GLUE_WAREHOUSE",
    "GLUE_DATABASE_SUFFIX",
    "S3_LANDING_BUCKET",
    "S3_REGION",
)


def main() -> int:
    # `--resource-id` es opcional (sirve para reprocesar un recurso suelto) y
    # getResolvedOptions falla si pide un argumento que no vino.
    opcionales = ["resource-id"] if "--resource-id" in sys.argv else []
    args = getResolvedOptions(
        sys.argv, ["dataset", "POSTGRES_DSN_SSM_PARAMETER", *ENV_ARGS, *opcionales]
    )
    for name in ENV_ARGS:
        os.environ[name] = args[name]
    # El DSN es secreto: llega por SSM y no por los argumentos del job.
    os.environ["POSTGRES_DSN"] = parameter_value(
        args["POSTGRES_DSN_SSM_PARAMETER"], args["S3_REGION"]
    )
    argv = ["--dataset", args["dataset"]]
    if args.get("resource_id"):
        argv += ["--resource-id", args["resource_id"]]
    return bronze_main(argv)


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0 (el run queda FAILED con "SystemExit: 0"). Solo se corta la
    # ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
