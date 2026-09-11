"""Bronze de reservas: del ZIP de landing a Iceberg, sin Spark.

El resto de bronze usa Spark porque los CSV del portal pesan cientos de MB. Acá el
archivo son 400 KB y el trabajo real es desarmar un cuadro de Excel con encabezados
fusionados, algo que Spark no sabe leer: levantar una JVM para eso sería pagar un job de
Spark entero por 40.000 filas. Se escribe con pyiceberg contra el mismo Glue Data Catalog
que usa Spark, así la tabla que queda es indistinguible de las que escribe Spark.

Corre como job de Glue Python shell (`pipelines/aws/bronze_reservas_job.py`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import boto3
import pyarrow as pa
from botocore.config import Config
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.expressions import EqualTo
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import DateType, NestedField, StringType, TimestampType
from sqlalchemy import desc, select

from pipelines.ingest.manifest import STATUS_OK, Manifest, ingestion_manifest
from pipelines.reservas.parser import LONG_COLUMNS, anio_from_name, parse_zip
from pipelines.spark_jobs.config import LakehouseConfig, load_config

logger = logging.getLogger("reservas.bronze_load")

DATASET = "reservas"
CATALOG = "lake"
NAMESPACE = "bronze"
TABLE = "reservas"

# Mismas columnas de linaje que agrega el bronze de Spark: una fila de silver se tiene que
# poder rastrear hasta el archivo de landing sin importar quién la escribió.
LINEAGE_COLUMNS = (
    "_resource_id",
    "_source_key",
    "_source_sha256",
    "_ingest_date",
    "_loaded_at",
    "data_origin",
)

DATA_ORIGIN = "real"


@dataclass(frozen=True)
class LandedZip:
    """Un ZIP anual ya en landing, tal como lo describe el manifiesto."""

    resource_id: str
    resource_name: str
    landing_key: str
    sha256: str
    ingest_date: date


def bronze_schema() -> Schema:
    """Todo string salvo las dos marcas de tiempo: bronze no tipa (lo hace silver)."""
    campos = [
        NestedField(indice, nombre, StringType(), required=False)
        for indice, nombre in enumerate(LONG_COLUMNS, start=1)
    ]
    siguiente = len(campos) + 1
    tipos = {"_ingest_date": DateType(), "_loaded_at": TimestampType()}
    campos += [
        NestedField(siguiente + offset, nombre, tipos.get(nombre, StringType()), required=False)
        for offset, nombre in enumerate(LINEAGE_COLUMNS)
    ]
    return Schema(*campos)


def partition_spec(schema: Schema) -> PartitionSpec:
    """Una partición por recurso, igual que el bronze de Spark."""
    campo = schema.find_field("_resource_id")
    return PartitionSpec(
        PartitionField(
            source_id=campo.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="_resource_id",
        )
    )


def open_catalog(config: LakehouseConfig) -> Catalog:
    """Glue Data Catalog, con los objetos en S3.

    Sin endpoint ni claves: boto3 y el FileIO de pyarrow resuelven solos las credenciales
    del rol del job. Es la misma configuración que `spark_jobs/session.py` le da a Spark.
    """
    return load_catalog(
        CATALOG,
        **{
            "type": "glue",
            "warehouse": config.glue_warehouse,
            "glue.region": config.s3_region,
            "s3.region": config.s3_region,
        },
    )


def open_landing(config: LakehouseConfig) -> object:
    """Cliente S3 apuntado a landing, con las credenciales del rol del job."""
    return boto3.client(
        "s3",
        region_name=config.s3_region,
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def read_manifest(manifest: Manifest, dataset: str = DATASET) -> list[LandedZip]:
    """Última corrida `ok` de cada recurso del dataset, por SQLAlchemy.

    A diferencia de los jobs de Spark (que leen por JDBC), acá están las dependencias de
    Python del repo y se usa el mismo modelo que escribe la ingesta.
    """
    tabla = ingestion_manifest
    consulta = (
        select(tabla)
        .where(tabla.c.dataset == dataset, tabla.c.status == STATUS_OK)
        .order_by(tabla.c.resource_id, desc(tabla.c.finished_at), desc(tabla.c.id))
    )
    ultimos: dict[str, LandedZip] = {}
    with manifest.engine.connect() as conn:
        for fila in conn.execute(consulta).mappings():
            if fila["resource_id"] in ultimos or not fila["landing_key"]:
                continue
            ultimos[fila["resource_id"]] = LandedZip(
                resource_id=fila["resource_id"],
                resource_name=fila["resource_name"],
                landing_key=fila["landing_key"],
                sha256=fila["sha256"],
                ingest_date=fila["ingest_date"],
            )
    return sorted(ultimos.values(), key=lambda zip_: zip_.resource_name)


def loaded_sha256(catalog: Catalog, identifier: str) -> dict[str, str]:
    """sha256 ya cargado por recurso. Vacío si la tabla todavía no existe."""
    if not catalog.table_exists(identifier):
        return {}
    scan = catalog.load_table(identifier).scan(selected_fields=("_resource_id", "_source_sha256"))
    # Cada partición se escribe entera de una vez, así que el sha es único por recurso.
    por_recurso = scan.to_arrow().group_by("_resource_id").aggregate([("_source_sha256", "max")])
    return dict(
        zip(
            por_recurso["_resource_id"].to_pylist(),
            por_recurso["_source_sha256_max"].to_pylist(),
        )
    )


def pending_files(landed: list[LandedZip], loaded: dict[str, str]) -> list[LandedZip]:
    """Recursos nuevos o cuyo sha256 cambió respecto de lo ya cargado en bronze."""
    return [zip_ for zip_ in landed if loaded.get(zip_.resource_id) != zip_.sha256]


def with_lineage(
    rows: list[dict[str, str]], file: LandedZip, loaded_at: datetime
) -> list[dict[str, object]]:
    """Agrega a cada fila larga de dónde salió y cuándo entró."""
    linaje = {
        "_resource_id": file.resource_id,
        "_source_key": file.landing_key,
        "_source_sha256": file.sha256,
        "_ingest_date": file.ingest_date,
        "_loaded_at": loaded_at,
        "data_origin": DATA_ORIGIN,
    }
    return [{**row, **linaje} for row in rows]


def to_arrow(rows: list[dict[str, object]], schema: pa.Schema) -> pa.Table:
    """Filas a Arrow con el esquema de la tabla, que trae los ids de campo de Iceberg."""
    columnas = {nombre: [fila[nombre] for fila in rows] for nombre in schema.names}
    return pa.Table.from_pydict(columnas, schema=schema)


def ensure_table(catalog: Catalog, identifier: str) -> Table:
    """Crea la tabla particionada por recurso la primera vez; después la abre."""
    # El namespace sale del identificador y no de la constante: ya viene con el sufijo del
    # ambiente pegado.
    catalog.create_namespace_if_not_exists(identifier.rsplit(".", 1)[0])
    schema = bronze_schema()
    return catalog.create_table_if_not_exists(
        identifier, schema=schema, partition_spec=partition_spec(schema)
    )


def write_partition(
    table: Table, rows: list[dict[str, object]], resource_id: str, replace: bool
) -> None:
    """Escribe la partición del recurso, reemplazando la anterior si el archivo cambió.

    Es la misma idempotencia que el bronze de Spark: el recurso nuevo se agrega y el que
    cambió de sha256 se reescribe entero, así nunca conviven filas de dos versiones del
    mismo archivo. Se distingue el caso en vez de usar siempre `overwrite` porque pyiceberg
    avisa por warning cuando el filtro de borrado no encuentra nada.
    """
    arrow = to_arrow(rows, table.schema().as_arrow())
    if replace:
        table.overwrite(arrow, overwrite_filter=EqualTo("_resource_id", resource_id))
        return
    table.append(arrow)


def load_resource(client: object, table: Table, file: LandedZip, bucket: str, replace: bool) -> int:
    """Descarga, parsea y escribe un ZIP anual; devuelve las filas largas que quedaron."""
    started = time.monotonic()
    data = client.get_object(Bucket=bucket, Key=file.landing_key)["Body"].read()
    anio = anio_from_name(file.resource_name)
    parsed = parse_zip(data, anio)
    if parsed.skipped_totals:
        logger.info(
            "%s: %d fila(s) de TOTAL descartadas (son la suma de la columna, no un yacimiento)",
            file.resource_name,
            parsed.skipped_totals,
        )
    # timezone.utc y no datetime.UTC: el job de Glue Python shell trae Python 3.9.
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    write_partition(table, with_lineage(parsed.rows, file, loaded_at), file.resource_id, replace)
    logger.info(
        "cargado %s | anio %d | %s filas largas | hojas %s | %.1f s",
        file.resource_id,
        anio,
        f"{len(parsed.rows):,}",
        ", ".join(parsed.sheets),
        time.monotonic() - started,
    )
    return len(parsed.rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga bronze de reservas desde landing")
    parser.add_argument("--resource-id", help="cargar un solo ZIP anual")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    config = load_config()
    # Mismo sufijo de ambiente que aplican los jobs de Spark (`with_suffix`): la base real
    # es `bronze_dev` o `bronze_prod` (ADR 0005).
    namespace = f"{NAMESPACE}{config.glue_database_suffix}"
    identifier = f"{namespace}.{TABLE}"

    catalog = open_catalog(config)
    landed = read_manifest(Manifest(config.postgres_dsn))
    if args.resource_id:
        landed = [file for file in landed if file.resource_id == args.resource_id]

    loaded = loaded_sha256(catalog, identifier)
    pending = pending_files(landed, loaded)
    logger.info("tabla=lake.%s recursos=%d pendientes=%d", identifier, len(landed), len(pending))
    if not pending:
        return 0

    started = time.monotonic()
    client = open_landing(config)
    table = ensure_table(catalog, identifier)
    total_rows = sum(
        load_resource(
            client, table, file, config.s3_landing_bucket, replace=file.resource_id in loaded
        )
        for file in pending
    )
    logger.info(
        "resumen: %d recursos cargados | %s filas | %.1f s",
        len(pending),
        f"{total_rows:,}",
        time.monotonic() - started,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
