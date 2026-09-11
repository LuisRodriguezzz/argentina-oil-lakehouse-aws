"""Job de Glue (Python shell) que carga la bronze de reservas.

El bronze de reservas no es Spark: el ZIP pesa 400 KB y el trabajo es desarmar un cuadro de
Excel con encabezados fusionados (`pipelines/reservas/bronze_load.py`). Corre entonces en el
mismo tipo de job que la ingesta, con el mismo truco del wheel, y escribe la tabla Iceberg
con pyiceberg contra el Glue Data Catalog.
"""

from __future__ import annotations

import logging
import os
import sys
import zipfile

import boto3
from awsglue.utils import getResolvedOptions

# Configuración del destino: la pone Terraform como argumentos por defecto del job.
ENV_ARGS = (
    "GLUE_WAREHOUSE",
    "GLUE_DATABASE_SUFFIX",
    "S3_LANDING_BUCKET",
    "S3_REGION",
)
PAQUETE_DIR = "/tmp/lakehouse-lib"

logger = logging.getLogger("bronze_reservas_job")


def instalar_paquete(uri: str) -> None:
    """Descomprime el wheel del proyecto en /tmp y lo pone en el path de módulos.

    Copia deliberada de `ingest_job.py`: los dos scripts corren antes de que el paquete
    exista, así que no hay de dónde importar la función. El motivo es el mismo: Glue Python
    shell instala con pip lo que llega por `--extra-py-files` y pip rechaza este wheel porque
    el proyecto pide Python >= 3.11 y acá corre 3.9.
    """
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    local = "/tmp/paquete.whl"
    boto3.client("s3").download_file(bucket, key, local)
    with zipfile.ZipFile(local) as wheel:
        wheel.extractall(PAQUETE_DIR)
    sys.path.insert(0, PAQUETE_DIR)


def main() -> int:
    # `force=True`: el runtime de Glue ya configuró el logging raíz antes de que corra este
    # script y sin eso basicConfig no hace nada y las líneas INFO nunca llegan a CloudWatch.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )

    # `--resource-id` es opcional y getResolvedOptions falla si pide un argumento que no vino.
    opcionales = ["resource-id"] if "--resource-id" in sys.argv else []
    args = getResolvedOptions(
        sys.argv, ["WHEEL_S3_URI", "POSTGRES_DSN_SSM_PARAMETER", *ENV_ARGS, *opcionales]
    )
    for name in ENV_ARGS:
        os.environ[name] = args[name]

    # Los imports van acá porque el paquete recién existe después de descomprimirlo.
    instalar_paquete(args["WHEEL_S3_URI"])
    from pipelines.aws.ssm import parameter_value
    from pipelines.reservas.bronze_load import main as bronze_main

    # El DSN es secreto: llega por SSM y no por los argumentos del job.
    os.environ["POSTGRES_DSN"] = parameter_value(
        args["POSTGRES_DSN_SSM_PARAMETER"], args["S3_REGION"]
    )
    # getResolvedOptions normaliza el guion a guion bajo en la clave.
    resource_id = args.get("resource_id")
    return bronze_main(["--resource-id", resource_id] if resource_id else [])


if __name__ == "__main__":
    # `bronze_main` hoy solo devuelve 0: la rama de `sys.exit` está por simetría con los
    # otros wrappers, donde `sys.exit(0)` sí haría que Glue marque el run como FAILED.
    codigo = main()
    if codigo:
        sys.exit(codigo)
