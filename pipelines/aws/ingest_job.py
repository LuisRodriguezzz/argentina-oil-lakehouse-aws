"""Job de Glue (Python shell) que corre la ingesta hacia landing/.

La ingesta es I/O de red: no necesita Spark, así que corre con 1/16 de DPU, lo más barato
que ofrece Glue. Este wrapper traduce los argumentos de Glue a variables de entorno y llama
al runner. No usa `pipelines.ingest.cli` porque typer necesita Python 3.10 y Python shell
trae 3.9.
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
    "S3_LANDING_BUCKET",
    "S3_LANDING_PREFIX",
    "S3_REGION",
    "POSTGRES_DSN_SSM_PARAMETER",
)
PAQUETE_DIR = "/tmp/lakehouse-lib"

logger = logging.getLogger("ingest_job")


def instalar_paquete(uri: str) -> None:
    """Descomprime el wheel del proyecto en /tmp y lo pone en el path de módulos.

    Glue Python shell instala con pip lo que llega por `--extra-py-files`, y pip rechaza
    este wheel porque el proyecto pide Python >= 3.11 (`Requires-Python`) y acá corre 3.9.
    El wheel es un zip: descomprimirlo alcanza y además deja los .yaml del repo como
    archivos de verdad, que es lo que espera `Path(...).read_text()`.
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

    # `--only` es opcional y getResolvedOptions falla si pide un argumento que no vino.
    opcionales = ["only"] if "--only" in sys.argv else []
    args = getResolvedOptions(sys.argv, ["dataset", "WHEEL_S3_URI", *ENV_ARGS, *opcionales])
    for name in ENV_ARGS:
        os.environ[name] = args[name]

    # Los imports van acá porque el paquete recién existe después de descomprimirlo.
    instalar_paquete(args["WHEEL_S3_URI"])
    from pipelines.ingest.ckan import CkanClient, build_session
    from pipelines.ingest.manifest import Manifest
    from pipelines.ingest.registry import get_dataset
    from pipelines.ingest.runner import run
    from pipelines.ingest.settings import load_settings
    from pipelines.ingest.storage import LandingStorage

    settings = load_settings()
    spec = get_dataset(args["dataset"])
    session = build_session()
    ckan = (
        CkanClient(settings.ckan_base_url, session=session) if spec.source_type == "ckan" else None
    )
    summary = run(
        spec,
        manifest=Manifest(settings.postgres_dsn),
        storage=LandingStorage.from_settings(settings),
        ckan=ckan,
        session=session,
        only=args.get("only"),
    )
    logger.info(
        "resumen dataset=%s ok=%d unchanged=%d failed=%d bytes_descargados=%d",
        summary.dataset,
        summary.ok,
        summary.unchanged,
        summary.failed,
        summary.downloaded_bytes,
    )
    return summary.exit_code


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0. Solo se corta la ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
