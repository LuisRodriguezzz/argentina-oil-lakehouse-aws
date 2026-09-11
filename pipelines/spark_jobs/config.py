"""Configuración de los jobs del lakehouse, leída del entorno.

No usa pydantic a propósito: Glue instala el wheel del proyecto con `--no-deps`, así que
este módulo tiene que funcionar con la stdlib sola. Toda la configuración llega por
variables de entorno; los wrappers de `pipelines/aws` las exportan desde los argumentos
que Terraform le pasa a cada job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LakehouseConfig:
    """Lo que los jobs necesitan saber del entorno donde corren."""

    s3_region: str
    s3_landing_bucket: str
    glue_warehouse: str
    glue_database_suffix: str
    postgres_dsn: str


def load_config() -> LakehouseConfig:
    """Lee la configuración del entorno, con defaults para lo que no es obligatorio."""
    return LakehouseConfig(
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
        # El bucket lo crea Terraform con el id de la cuenta en el nombre: no hay un default útil.
        s3_landing_bucket=os.environ.get("S3_LANDING_BUCKET", ""),
        # Raíz S3 donde el Glue Data Catalog guarda las tablas Iceberg.
        glue_warehouse=os.environ.get("GLUE_WAREHOUSE", ""),
        # Sufijo del ambiente para las bases del catálogo: `bronze` -> `bronze_dev`. Lo pone
        # Terraform como argumento del job (ADR 0005): dev y prod comparten cuenta y el Glue
        # Data Catalog no admite dos bases `bronze`.
        glue_database_suffix=os.environ.get("GLUE_DATABASE_SUFFIX", ""),
        postgres_dsn=os.environ.get("POSTGRES_DSN", ""),
    )
