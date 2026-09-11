"""Tests de la configuración de los jobs, que llega solo por variables de entorno."""

from __future__ import annotations

from pipelines.spark_jobs.config import load_config

VARIABLES = (
    "S3_REGION",
    "S3_LANDING_BUCKET",
    "GLUE_WAREHOUSE",
    "GLUE_DATABASE_SUFFIX",
    "POSTGRES_DSN",
)


def limpiar_entorno(monkeypatch) -> None:
    """El entorno de quien corre los tests no tiene que cambiar el resultado."""
    for nombre in VARIABLES:
        monkeypatch.delenv(nombre, raising=False)


def test_load_config_lee_el_entorno(monkeypatch):
    limpiar_entorno(monkeypatch)
    monkeypatch.setenv("S3_REGION", "sa-east-1")
    monkeypatch.setenv("S3_LANDING_BUCKET", "lakehouse-123")
    monkeypatch.setenv("GLUE_WAREHOUSE", "s3://lakehouse-123/warehouse")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://u:p@host/lakehouse")

    config = load_config()
    assert config.s3_region == "sa-east-1"
    assert config.s3_landing_bucket == "lakehouse-123"
    assert config.glue_warehouse == "s3://lakehouse-123/warehouse"
    assert config.postgres_dsn == "postgresql://u:p@host/lakehouse"


def test_load_config_cae_a_los_valores_por_defecto(monkeypatch):
    limpiar_entorno(monkeypatch)
    config = load_config()
    assert config.s3_region == "us-east-1"
    assert config.s3_landing_bucket == ""
    assert config.glue_warehouse == ""
    assert config.postgres_dsn == ""


def test_load_config_sin_sufijo_de_ambiente_por_defecto(monkeypatch):
    limpiar_entorno(monkeypatch)
    assert load_config().glue_database_suffix == ""


def test_load_config_lee_el_sufijo_de_ambiente(monkeypatch):
    # Lo pone Terraform como argumento del job (ADR 0005).
    monkeypatch.setenv("GLUE_DATABASE_SUFFIX", "_prod")
    assert load_config().glue_database_suffix == "_prod"
