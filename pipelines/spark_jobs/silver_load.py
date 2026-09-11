"""Silver: aplica un contrato de datos sobre bronze y escribe Iceberg tipado.

Por cada recurso pendiente: castea las columnas al tipo del contrato, manda a cuarentena
las filas que violan un rango, deduplica por clave primaria y reemplaza las particiones
afectadas. Los checks duros (nulos donde el contrato no los permite, columnas faltantes,
duplicados, demasiados rechazos) hacen fallar el job con código 1 y quedan registrados en
`dq_runs`. El contrato vive en `pipelines/contracts/*.yaml` (ADR 0002).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from pipelines.spark_jobs.bronze_rules import namespace_of
from pipelines.spark_jobs.config import load_config
from pipelines.spark_jobs.session import build_spark
from pipelines.spark_jobs.silver_rules import (
    Contract,
    Measures,
    RunReport,
    column_names,
    contract_names,
    dq_runs_table,
    hard_failures,
    load_contract,
    missing_columns,
    pending_resources,
    reject_reason_expression,
    rejects_table,
    required_columns,
    run_status,
    select_expressions,
)

logger = logging.getLogger("silver_load")

# Linaje que silver hereda de bronze, para poder rastrear una fila hasta el CSV de origen.
LINEAGE = ["_resource_id", "_source_sha256", "data_origin"]

DQ_RUNS_SCHEMA = StructType(
    [
        StructField("run_at", TimestampType(), False),
        StructField("contract", StringType(), False),
        StructField("resource_id", StringType(), False),
        StructField("rows_in", LongType(), False),
        StructField("rows_out", LongType(), False),
        StructField("rows_rejected", LongType(), False),
        StructField("hard_failures", StringType(), True),
        StructField("status", StringType(), False),
    ]
)


def sha256_by_resource(spark: SparkSession, table: str) -> dict[str, str]:
    """sha256 cargado por recurso. Vacío si la tabla todavía no existe."""
    if not spark.catalog.tableExists(table):
        return {}
    # Cada recurso se escribe entero de una vez, así que el sha es único por recurso.
    rows = (
        spark.table(table)
        .groupBy("_resource_id")
        .agg(F.max("_source_sha256").alias("sha256"))
        .collect()
    )
    return {row["_resource_id"]: row["sha256"] for row in rows}


def flag_rejects(spark: SparkSession, contract: Contract, resource_id: str) -> DataFrame:
    """Filas de bronze del recurso con una columna `reject_reason` (vacía si está bien)."""
    source = spark.table(contract.source).filter(F.col("_resource_id") == resource_id)
    return source.withColumn("reject_reason", F.expr(reject_reason_expression(contract)))


def typed_rows(flagged: DataFrame, contract: Contract) -> DataFrame:
    """Filas aceptadas, casteadas al tipo del contrato y con el linaje de bronze."""
    accepted = flagged.filter("reject_reason = ''")
    return accepted.selectExpr(*select_expressions(contract), *LINEAGE).withColumn(
        "_silver_loaded_at", F.current_timestamp()
    )


def rejected_rows(flagged: DataFrame, contract: Contract) -> DataFrame:
    """Filas rechazadas con sus strings originales: la cuarentena es para auditar."""
    original = [f"`{name}`" for name in column_names(contract)]
    return flagged.filter("reject_reason <> ''").selectExpr(
        "reject_reason",
        "current_timestamp() AS _rejected_at",
        *original,
        *LINEAGE,
    )


def deduplicate(df: DataFrame, contract: Contract) -> DataFrame:
    """Una fila por clave primaria: gana la de `dedupe_by` más alto (la rectificativa)."""
    if not contract.dedupe_by:
        return df.dropDuplicates(list(contract.primary_key))
    orden = Window.partitionBy(*contract.primary_key).orderBy(
        F.col(contract.dedupe_by).desc_nulls_last()
    )
    return df.withColumn("_orden", F.row_number().over(orden)).filter("_orden = 1").drop("_orden")


def measure(df: DataFrame, contract: Contract) -> Measures:
    """Filas, claves distintas y nulos por columna obligatoria, en una sola pasada."""
    obligatorias = required_columns(contract)
    aggregations = [
        F.count(F.lit(1)).alias("filas"),
        F.count_distinct(*[F.col(name) for name in contract.primary_key]).alias("claves"),
    ]
    aggregations += [
        F.count(F.when(F.col(name).isNull(), 1)).alias(f"nulos_{name}") for name in obligatorias
    ]
    row = df.agg(*aggregations).first()
    return Measures(
        rows=row["filas"],
        keys=row["claves"],
        nulls={name: row[f"nulos_{name}"] for name in obligatorias},
    )


def write_partitions(
    spark: SparkSession,
    df: DataFrame,
    table: str,
    partition_by: list[str],
) -> None:
    """Crea la tabla la primera vez; después reemplaza solo las particiones que llegan."""
    if spark.catalog.tableExists(table):
        df.writeTo(table).overwritePartitions()
        return
    writer = df.writeTo(table).using("iceberg")
    if partition_by:
        writer = writer.partitionedBy(*[F.col(name) for name in partition_by])
    writer.create()


def load_resource(spark: SparkSession, contract: Contract, resource_id: str) -> RunReport:
    """Procesa un recurso completo y devuelve qué pasó con él."""
    started = time.monotonic()
    report = RunReport(resource_id=resource_id)
    # Se cachea porque se recorre tres veces: contar, separar rechazos y castear.
    flagged = flag_rejects(spark, contract, resource_id).cache()
    try:
        report.rows_in = flagged.count()
        rejected = rejected_rows(flagged, contract)
        report.rows_rejected = rejected.count()
        if report.rows_rejected:
            write_partitions(spark, rejected, rejects_table(contract), ["_resource_id"])

        accepted = deduplicate(typed_rows(flagged, contract), contract)
        measures = measure(accepted, contract)
        report.rows_out = measures.rows
        report.hard_failures = hard_failures(measures, report.rows_in, report.rows_rejected)
        if report.hard_failures:
            logger.error("%s NO se escribe: %s", resource_id, " | ".join(report.hard_failures))
            return report
        write_partitions(spark, accepted, contract.table, list(contract.partition_by))
    finally:
        flagged.unpersist()
    logger.info(
        "%s | in %s | out %s | rechazadas %s | %.1f s",
        resource_id,
        f"{report.rows_in:,}",
        f"{report.rows_out:,}",
        f"{report.rows_rejected:,}",
        time.monotonic() - started,
    )
    return report


def record_runs(spark: SparkSession, contract: Contract, reports: list[RunReport]) -> None:
    """Historial de calidad: una fila por recurso y corrida, para poder consultarlo."""
    if not reports:
        return
    now = datetime.now(UTC)
    rows = [
        (
            now,
            contract.name,
            report.resource_id,
            report.rows_in,
            report.rows_out,
            report.rows_rejected,
            " | ".join(report.hard_failures),
            run_status(report),
        )
        for report in reports
    ]
    df = spark.createDataFrame(rows, schema=DQ_RUNS_SCHEMA)
    table = dq_runs_table(contract)
    if spark.catalog.tableExists(table):
        df.writeTo(table).append()
        return
    df.writeTo(table).using("iceberg").create()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga la capa silver aplicando un contrato")
    parser.add_argument("--contract", required=True, choices=contract_names())
    parser.add_argument("--resource-id", help="procesar un solo recurso del origen")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    config = load_config()
    contract = load_contract(args.contract, suffix=config.glue_database_suffix)

    spark = build_spark(f"silver_load:{contract.name}", config)
    started = time.monotonic()
    reports: list[RunReport] = []
    try:
        if not spark.catalog.tableExists(contract.source):
            logger.error("no existe la tabla de origen %s: correr bronze_load", contract.source)
            return 1
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {namespace_of(contract.table)}")

        # Check duro de esquema: sin las columnas del contrato no hay nada que hacer.
        missing = missing_columns(contract, spark.table(contract.source).columns)
        if missing:
            reports = [RunReport(resource_id="*", hard_failures=[f"faltan columnas: {missing}"])]
            logger.error("%s no tiene las columnas %s", contract.source, missing)
        else:
            pending = pending_resources(
                sha256_by_resource(spark, contract.source),
                sha256_by_resource(spark, contract.table),
            )
            if args.resource_id:
                pending = [item for item in pending if item == args.resource_id]
            logger.info(
                "contrato=%s origen=%s destino=%s pendientes=%d",
                contract.name,
                contract.source,
                contract.table,
                len(pending),
            )
            for resource_id in pending:
                reports.append(load_resource(spark, contract, resource_id))

        record_runs(spark, contract, reports)
        logger.info(
            "resumen: %d recursos | %s filas | %s rechazadas | %d con fallas duras | %.1f s",
            len(reports),
            f"{sum(report.rows_out for report in reports):,}",
            f"{sum(report.rows_rejected for report in reports):,}",
            sum(1 for report in reports if run_status(report) == "failed"),
            time.monotonic() - started,
        )
    finally:
        spark.stop()
    return 1 if any(run_status(report) == "failed" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
