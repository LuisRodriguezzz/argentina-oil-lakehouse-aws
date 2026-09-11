"""Reglas del job silver que no dependen de Spark (se testean sin JVM).

Acá vive el contrato de datos: cómo se lee del YAML, qué expresión SQL castea cada
columna y qué expresión marca una fila como rechazada. El job de Spark solo aplica lo
que estas funciones devuelven, así que la lógica de calidad se puede testear sin JVM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pipelines.spark_jobs.bronze_rules import load_yaml_file, namespace_of, with_suffix

# pipelines/spark_jobs/silver_rules.py -> pipelines/contracts
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

# Tipos del contrato y su equivalente en Spark SQL. `boolean` no está: se arma a mano
# porque los CSV traen 't'/'f' y un cast directo daría null.
SPARK_TYPES = {
    "int": "INT",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}
CONTRACT_TYPES = (*SPARK_TYPES, "string", "boolean")

# Si más del 1 % de las filas de un recurso se rechaza, el problema es de la fuente o del
# contrato, no de unas filas sueltas: mejor frenar y mirar que publicar datos a medias.
REJECT_THRESHOLD = 0.01


@dataclass(frozen=True)
class ContractColumn:
    """Una columna del contrato, con su tipo y sus reglas de rango."""

    name: str
    type: str
    nullable: bool
    description: str
    minimum: float | None = None
    maximum: float | None = None
    allowed_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contract:
    """El contrato completo de una tabla silver."""

    name: str
    table: str
    source: str
    primary_key: tuple[str, ...]
    partition_by: tuple[str, ...]
    dedupe_by: str | None
    columns: tuple[ContractColumn, ...]


@dataclass
class RunReport:
    """Lo que pasó con un recurso; es también la fila que se guarda en `dq_runs`."""

    resource_id: str
    rows_in: int = 0
    rows_out: int = 0
    rows_rejected: int = 0
    hard_failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Measures:
    """Lo que se mide sobre las filas aceptadas para decidir si pueden entrar a silver."""

    rows: int
    keys: int
    nulls: dict[str, int]


def run_status(report: RunReport) -> str:
    """`failed` si el recurso no pasó algún check duro."""
    return "failed" if report.hard_failures else "ok"


def hard_failures(measures: Measures, rows_in: int, rows_rejected: int) -> list[str]:
    """Motivos por los que un recurso no puede entrar a silver."""
    failures = [
        f"{name}: {count} nulos en una columna no nullable"
        for name, count in measures.nulls.items()
        if count
    ]
    if measures.rows != measures.keys:
        failures.append(f"clave primaria duplicada: {measures.rows} filas y {measures.keys} claves")
    if too_many_rejects(rows_in, rows_rejected):
        failures.append(f"rechazos {rows_rejected / rows_in:.2%} sobre el umbral tolerado")
    return failures


def contract_path(name: str, directory: Path | str | None = None) -> Path:
    """Ruta del YAML de un contrato por nombre."""
    return Path(directory or CONTRACTS_DIR) / f"{name}.yaml"


def contract_names(directory: Path | str | None = None) -> list[str]:
    """Contratos disponibles, para las opciones de la CLI."""
    return sorted(path.stem for path in Path(directory or CONTRACTS_DIR).glob("*.yaml"))


def load_contract(name: str, directory: Path | str | None = None, suffix: str = "") -> Contract:
    """Lee el contrato del YAML y valida que sea coherente.

    `suffix` es el del ambiente (`_dev`, `_prod`): el contrato nombra `lake.silver.fractura`
    y la base real es `silver_dev`. Ver `with_suffix` y el ADR 0005.
    """
    raw = load_yaml_file(contract_path(name, directory))
    contract = Contract(
        name=name,
        table=with_suffix(raw["table"], suffix),
        source=with_suffix(raw["source"], suffix),
        primary_key=tuple(raw["primary_key"]),
        partition_by=tuple(raw.get("partition_by") or ()),
        dedupe_by=raw.get("dedupe_by"),
        columns=tuple(build_column(entry) for entry in raw["columns"]),
    )
    check_contract(contract)
    return contract


def build_column(entry: dict) -> ContractColumn:
    """Una entrada de `columns:` del YAML."""
    return ContractColumn(
        name=entry["name"],
        type=entry["type"],
        nullable=bool(entry.get("nullable", True)),
        description=entry.get("description", ""),
        minimum=entry.get("min"),
        maximum=entry.get("max"),
        allowed_values=tuple(entry.get("allowed_values") or ()),
    )


def check_contract(contract: Contract) -> None:
    """Errores de escritura del contrato: mejor acá que a mitad del job."""
    names = column_names(contract)
    if len(names) != len(set(names)):
        raise ValueError(f"{contract.name}: hay columnas repetidas")
    for column in contract.columns:
        if column.type not in CONTRACT_TYPES:
            raise ValueError(f"{contract.name}.{column.name}: tipo invalido {column.type!r}")
    for referenced in (*contract.primary_key, *contract.partition_by):
        if referenced not in names:
            raise ValueError(f"{contract.name}: {referenced!r} no esta en columns")
    if contract.dedupe_by and contract.dedupe_by not in names:
        raise ValueError(f"{contract.name}: dedupe_by {contract.dedupe_by!r} no esta en columns")
    # `measure()` cuenta las claves con count_distinct, que descarta los nulos: con una clave
    # nullable el check duro de duplicados falla sin que haya duplicados reales.
    por_nombre = {column.name: column for column in contract.columns}
    for key in contract.primary_key:
        if por_nombre[key].nullable:
            raise ValueError(f"{contract.name}: la clave primaria {key!r} no puede ser nullable")


def column_names(contract: Contract) -> list[str]:
    """Nombres de las columnas del contrato, en orden."""
    return [column.name for column in contract.columns]


def required_columns(contract: Contract) -> list[str]:
    """Columnas que el contrato declara no nulas."""
    return [column.name for column in contract.columns if not column.nullable]


def missing_columns(contract: Contract, available: list[str]) -> list[str]:
    """Columnas del contrato que la tabla de origen no tiene (check duro)."""
    return [name for name in column_names(contract) if name not in available]


def rejects_table(contract: Contract) -> str:
    """Tabla de cuarentena: la misma que silver con sufijo `_rejects`."""
    return f"{contract.table}_rejects"


def dq_runs_table(contract: Contract) -> str:
    """Historial de corridas, uno por namespace de silver."""
    return f"{namespace_of(contract.table)}.dq_runs"


def cast_expression(column: ContractColumn) -> str:
    """Expresión SQL que convierte la columna string de bronze al tipo del contrato."""
    # Bronze guarda strings crudos: un espacio sobrante o un "" no son un valor.
    source = f"nullif(trim(`{column.name}`), '')"
    if column.type == "string":
        return source
    if column.type == "boolean":
        # Los CSV del portal traen 't'/'f'; cualquier otra cosa queda nula.
        return f"CASE lower({source}) WHEN 't' THEN true WHEN 'f' THEN false END"
    # try_cast y no cast: con ANSI activado (Spark 4) un cast invalido aborta el job, y
    # una fila mal formada no puede tirar abajo la carga entera.
    return f"try_cast({source} AS {SPARK_TYPES[column.type]})"


def select_expressions(contract: Contract) -> list[str]:
    """Un `expr AS nombre` por columna del contrato, en el orden declarado."""
    return [f"{cast_expression(column)} AS `{column.name}`" for column in contract.columns]


def reject_rules(contract: Contract) -> list[tuple[str, str]]:
    """Pares (condición SQL que es verdadera cuando la fila viola la regla, motivo).

    Las condiciones castean la columna en el momento, así que se evalúan sobre bronze:
    la fila rechazada se guarda con sus strings originales, que es lo que hace falta para
    entender qué llegó mal.
    """
    rules = []
    for column in contract.columns:
        value = cast_expression(column)
        if column.minimum is not None:
            motivo = f"{column.name} menor que {column.minimum}"
            rules.append((f"{value} < {column.minimum}", motivo))
        if column.maximum is not None:
            motivo = f"{column.name} mayor que {column.maximum}"
            rules.append((f"{value} > {column.maximum}", motivo))
        if column.allowed_values:
            allowed = ", ".join(sql_string(item) for item in column.allowed_values)
            rules.append((f"{value} NOT IN ({allowed})", f"{column.name} fuera de allowed_values"))
    return rules


def reject_reason_expression(contract: Contract) -> str:
    """Motivos de rechazo de la fila separados por `; `, o vacío si no viola nada.

    `concat_ws` ignora los nulos, así que una regla que no se cumple (o que se evalúa
    sobre un valor nulo) no aporta texto.
    """
    rules = reject_rules(contract)
    if not rules:
        return "''"
    branches = ", ".join(
        f"CASE WHEN {condition} THEN {sql_string(reason)} END" for condition, reason in rules
    )
    return f"concat_ws('; ', {branches})"


def sql_string(value: str) -> str:
    """Literal SQL de un string, con las comillas simples escapadas."""
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def pending_resources(
    source_sha256: dict[str, str],
    loaded_sha256: dict[str, str],
) -> list[str]:
    """Recursos de bronze nuevos o con hash distinto del que ya está en silver."""
    return sorted(
        resource_id
        for resource_id, sha256 in source_sha256.items()
        if loaded_sha256.get(resource_id) != sha256
    )


def too_many_rejects(rows_in: int, rows_rejected: int) -> bool:
    """True si los rechazos superan el umbral tolerado del recurso."""
    return rows_in > 0 and rows_rejected / rows_in > REJECT_THRESHOLD
