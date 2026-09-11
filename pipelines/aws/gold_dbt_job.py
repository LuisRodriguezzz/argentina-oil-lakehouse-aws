"""Job de Glue que construye la capa gold con dbt sobre Athena (ADR 0003).

dbt corre con `dbt-athena`, que no necesita ningún motor propio: manda SQL a Athena y Athena
lee y escribe las mismas tablas Iceberg del Glue Data Catalog.

Va sobre Glue 5.0 (Spark) y no sobre Python shell porque Python shell sigue en 3.9 y dbt-core
dejó de soportarlo en la 1.11. Spark no se usa: el único trabajo del job es hablar con Athena.

El proyecto de dbt viaja adentro del wheel (`pipelines/dbt/`), que Glue instala con pip: por
eso `dbt_project.yml` y `profiles.yml` son archivos de verdad y no entradas de un zip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from awsglue.utils import getResolvedOptions

import pipelines

# Variables que lee el target `aws` de profiles.yml. Las pone Terraform como argumentos por
# defecto del job; acá solo se traducen a os.environ, que es donde las busca `env_var()`.
ENV_ARGS = (
    "AWS_REGION",
    "S3_STAGING_DIR",
    "S3_DATA_DIR",
    "ATHENA_WORKGROUP",
    "ATHENA_DATABASE",
    # Sufijo del ambiente. `profiles.yml` arma con él el schema de gold y `sources.yml`
    # el de silver, así los mismos modelos escriben en `gold_dev` o en `gold_prod`.
    "GLUE_DATABASE_SUFFIX",
)

PROJECT_DIR = Path(pipelines.__file__).resolve().parent / "dbt"
# El directorio del paquete es de solo lectura para el job; target/ y logs/ van a /tmp.
ARTIFACTS_DIR = Path("/tmp/dbt")


def main() -> int:
    args = getResolvedOptions(sys.argv, [*ENV_ARGS])
    for name in ENV_ARGS:
        os.environ[name] = args[name]

    # Dónde deja dbt sus artefactos. Van por entorno y no como argumento; dbt las lee al
    # armar los flags dentro de `invoke()`, no al importarse, así que basta fijarlas antes.
    os.environ["DBT_TARGET_PATH"] = str(ARTIFACTS_DIR / "target")
    os.environ["DBT_LOG_PATH"] = str(ARTIFACTS_DIR / "logs")

    from dbt.cli.main import dbtRunner

    # `build` y no `run` + `test`: corre modelos y tests en el orden del grafo y frena la rama
    # si un test falla, así una tabla que no pasa sus tests no propaga el error hacia arriba.
    result = dbtRunner().invoke(
        [
            "build",
            "--target",
            "aws",
            "--project-dir",
            str(PROJECT_DIR),
            "--profiles-dir",
            str(PROJECT_DIR),
        ]
    )
    return 0 if result.success else 1


if __name__ == "__main__":
    # `sys.exit(0)` no: Glue toma cualquier SystemExit del script como fallo del job,
    # incluso con código 0. Solo se corta la ejecución cuando el job de verdad falló.
    codigo = main()
    if codigo:
        sys.exit(codigo)
