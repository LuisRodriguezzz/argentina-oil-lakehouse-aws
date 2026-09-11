"""Job de Glue (Spark) que carga la capa silver aplicando un contrato.

Wrapper fino: lee los argumentos de Glue, los exporta a `os.environ` y llama al job.
Silver no toca ni landing ni el manifiesto, así que no necesita el DSN.
"""

from __future__ import annotations

import os
import sys

from awsglue.utils import getResolvedOptions

from pipelines.spark_jobs.silver_load import main as silver_main

ENV_ARGS = ("GLUE_WAREHOUSE", "GLUE_DATABASE_SUFFIX", "S3_REGION")


def main() -> int:
    args = getResolvedOptions(sys.argv, ["contract", *ENV_ARGS])
    for name in ENV_ARGS:
        os.environ[name] = args[name]
    return silver_main(["--contract", args["contract"]])


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0 (el run queda FAILED con "SystemExit: 0"). Solo se corta la
    # ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
