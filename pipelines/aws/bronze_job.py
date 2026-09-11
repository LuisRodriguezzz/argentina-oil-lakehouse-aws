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
    args = getResolvedOptions(sys.argv, ["dataset", "POSTGRES_DSN_SSM_PARAMETER", *ENV_ARGS])
    for name in ENV_ARGS:
        os.environ[name] = args[name]
    # El DSN es secreto: llega por SSM y no por los argumentos del job.
    os.environ["POSTGRES_DSN"] = parameter_value(
        args["POSTGRES_DSN_SSM_PARAMETER"], args["S3_REGION"]
    )
    return bronze_main(["--dataset", args["dataset"]])


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0 (el run queda FAILED con "SystemExit: 0"). Solo se corta la
    # ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
