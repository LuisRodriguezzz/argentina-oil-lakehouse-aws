"""Estado de una capa del lakehouse, leído del Glue Data Catalog sin Spark ni Java.

Usa pyiceberg con las credenciales y la región del perfil de `~/.aws` del que lo corre.
Uso: uv run python scripts/check_lake.py --namespace silver --suffix _prod
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import UTC

from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.table import Table

DQ_RUNS = "dq_runs"
REJECTS_SUFFIX = "_rejects"


def open_catalog() -> Catalog:
    """Glue Data Catalog. boto3 resuelve región y credenciales del perfil activo."""
    return load_catalog("lake", **{"type": "glue"})


def table_names(catalog: Catalog, database: str) -> list[str]:
    """Nombres de tabla de la base, ordenados."""
    return sorted(identifier[-1] for identifier in catalog.list_tables(database))


def partition_label(row: dict) -> str:
    """`{'anio': 2024}` -> `anio=2024`. Una tabla sin particiones no trae claves."""
    values = row.get("partition") or {}
    return ", ".join(f"{key}={value}" for key, value in values.items()) or "(sin particion)"


def print_partitions(table: Table, name: str) -> None:
    """Filas por partición leídas de la metadata (no escanea los datos)."""
    rows = table.inspect.partitions().to_pylist()
    columns = len(table.schema().fields)
    print(f"\n{name}: {columns} columnas · {len(rows)} particion(es)")
    total = 0
    for row in sorted(rows, key=partition_label):
        total += row["record_count"]
        archivos = f"({row['file_count']} arch.)"
        print(f"  {partition_label(row):<50}{row['record_count']:>12,}  {archivos}")
    print(f"  {'total':<50}{total:>12,}")


def print_last_snapshot(table: Table) -> None:
    """Última escritura de la tabla, para saber si el job corrió."""
    snapshots = table.inspect.snapshots().to_pylist()
    if not snapshots:
        return
    last = snapshots[-1]
    committed = last["committed_at"].astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    # `summary` es un map de Arrow: llega como lista de pares (clave, valor).
    summary = dict(last["summary"] or [])
    agregadas = summary.get("added-records", "-")
    print(f"  ultimo snapshot: {committed}Z {last['operation']} +{agregadas} filas")


def print_dq_runs(table: Table, limit: int = 10) -> None:
    """Historial de calidad: las últimas corridas registradas por el job silver."""
    runs = table.scan().to_arrow().to_pylist()
    runs.sort(key=lambda run: run["run_at"])
    print(f"\ndq_runs: {len(runs)} corrida(s), ultimas {min(limit, len(runs))}")
    encabezado = f"{'run_at':<20}{'contrato':<26}{'recurso':<14}{'in':>10}{'out':>10}{'rech':>7}"
    print(f"  {encabezado}  estado")
    for run in runs[-limit:]:
        momento = run["run_at"].astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"  {momento:<20}{run['contract']:<26}{run['resource_id'][:12]:<14}"
            f"{run['rows_in']:>10,}{run['rows_out']:>10,}{run['rows_rejected']:>7,}"
            f"  {run['status']}{'  ' + run['hard_failures'] if run['hard_failures'] else ''}"
        )


def print_rejects(table: Table, name: str) -> None:
    """Cuarentena agrupada por motivo: qué regla del contrato se está violando."""
    reasons = table.scan(selected_fields=("reject_reason",)).to_arrow().column(0).to_pylist()
    print(f"\n{name}: {len(reasons)} fila(s) en cuarentena")
    for reason, count in sorted(Counter(reasons).items(), key=lambda item: -item[1]):
        print(f"  {count:>6,}  {reason}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Muestra el estado de una capa del lakehouse")
    parser.add_argument("--namespace", default="bronze", help="bronze, silver o gold")
    # La base del catálogo lleva el ambiente: `silver` + `_prod` -> `silver_prod`.
    parser.add_argument("--suffix", default=os.environ.get("GLUE_DATABASE_SUFFIX", ""))
    parser.add_argument("--table", help="mostrar una sola tabla del namespace")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database = f"{args.namespace}{args.suffix}"
    catalog = open_catalog()

    names = table_names(catalog, database)
    if args.table:
        names = [name for name in names if name == args.table]
    print(f"glue data catalog · base {database}: {len(names)} tabla(s)")
    for name in names:
        print(f"  - {name}")

    for name in names:
        table = catalog.load_table(f"{database}.{name}")
        if name == DQ_RUNS:
            print_dq_runs(table)
        elif name.endswith(REJECTS_SUFFIX):
            print_rejects(table, name)
        else:
            print_partitions(table, name)
            print_last_snapshot(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
