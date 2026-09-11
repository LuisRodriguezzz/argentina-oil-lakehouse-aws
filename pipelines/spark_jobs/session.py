"""Construcción de la SparkSession con el catálogo Iceberg `lake` sobre Glue."""

from __future__ import annotations

from pyspark.sql import SparkSession

from pipelines.spark_jobs.config import LakehouseConfig, load_config

# Los jars de Iceberg los pone el runtime de Glue con `--datalake-formats iceberg`, y el
# master lo fija Glue (YARN): acá solo se declara el catálogo.
CATALOG = "lake"
ICEBERG_EXTENSIONS = "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"


def build_spark(app_name: str, config: LakehouseConfig | None = None) -> SparkSession:
    """SparkSession con el catálogo `lake` apuntando al Glue Data Catalog.

    Sin endpoint, sin path-style y sin claves: S3FileIO resuelve todo eso con las
    credenciales del rol del job.
    """
    conf = config or load_config()
    catalog = f"spark.sql.catalog.{CATALOG}"
    return (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.extensions", ICEBERG_EXTENSIONS)
        .config(catalog, "org.apache.iceberg.spark.SparkCatalog")
        .config(f"{catalog}.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
        .config(f"{catalog}.warehouse", conf.glue_warehouse)
        .config(f"{catalog}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config("spark.sql.defaultCatalog", CATALOG)
        .getOrCreate()
    )
