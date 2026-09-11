"""Manifiesto de ingesta: una fila por intento de ingesta de un recurso."""

from __future__ import annotations

import logging
import socket
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_UNCHANGED = "unchanged"
STATUS_FAILED = "failed"

metadata = MetaData()

# `last_modified_source` se guarda como texto: CKAN devuelve ISO sin zona y las fuentes
# http devuelven fecha HTTP; se compara por igualdad exacta, nunca por orden temporal.
ingestion_manifest = Table(
    "ingestion_manifest",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset", String(64), nullable=False),
    Column("source_type", String(16), nullable=False),
    Column("resource_id", String(128), nullable=False),
    Column("resource_name", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("size_bytes_source", BigInteger),
    Column("last_modified_source", String(64)),
    Column("sha256", String(64)),
    Column("size_bytes_landed", BigInteger),
    Column("landing_key", Text),
    Column("ingest_date", Date, nullable=False),
    Column("status", String(16), nullable=False),
    Column("error", Text),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Index("ix_ingestion_manifest_dataset_resource", "dataset", "resource_id"),
)


def _now() -> datetime:
    # timezone.utc y no datetime.UTC: la ingesta corre en un job de Glue Python shell, que
    # trae Python 3.9, aunque el repo pida 3.11 para el host.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dsn_for_sqlalchemy(dsn: str) -> str:
    """Fuerza el driver psycopg3 (psycopg2 no esta instalado)."""
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+psycopg://", 1)
    return dsn


def _ipv4_del_servidor(dsn: str) -> str | None:
    """Primera direccion IPv4 del host del DSN, o None si no hay host o no resuelve.

    Neon publica direcciones IPv4 e IPv6 y el cliente toma la primera que le da el DNS.
    Los jobs Python shell de Glue no tienen salida IPv6, y la conexion muere con
    "Cannot assign requested address". Pedirle al DNS solo IPv4 evita la loteria.
    """
    host = urlsplit(dsn).hostname
    if not host:
        return None
    try:
        direcciones = socket.getaddrinfo(host, 5432, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    return direcciones[0][4][0] if direcciones else None


def _connect_args(dsn: str) -> dict[str, str]:
    """`hostaddr` fija la IP a la que se conecta psycopg; `host` sigue en el DSN para el SNI."""
    ipv4 = _ipv4_del_servidor(dsn) if dsn.startswith(("postgresql://", "postgres://")) else None
    return {"hostaddr": ipv4} if ipv4 else {}


class Manifest:
    """Acceso al manifiesto. Funciona igual con Postgres y con SQLite."""

    def __init__(self, dsn: str, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(
            _dsn_for_sqlalchemy(dsn), future=True, connect_args=_connect_args(dsn)
        )
        metadata.create_all(self.engine)

    def latest_ok(self, dataset: str, resource_id: str) -> dict[str, Any] | None:
        """Ultima corrida con status `ok` (la ultima descarga real) del recurso."""
        stmt = (
            select(ingestion_manifest)
            .where(
                ingestion_manifest.c.dataset == dataset,
                ingestion_manifest.c.resource_id == resource_id,
                ingestion_manifest.c.status == STATUS_OK,
            )
            .order_by(desc(ingestion_manifest.c.finished_at), desc(ingestion_manifest.c.id))
            .limit(1)
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return dict(row) if row else None

    def start(
        self,
        *,
        dataset: str,
        source_type: str,
        resource_id: str,
        resource_name: str,
        url: str,
        size_bytes_source: int | None,
        last_modified_source: str | None,
        ingest_date: date,
        landing_key: str | None = None,
    ) -> int:
        """Abre una fila en estado `failed` (pesimista) y devuelve su id."""
        stmt = insert(ingestion_manifest).values(
            dataset=dataset,
            source_type=source_type,
            resource_id=resource_id,
            resource_name=resource_name,
            url=url,
            size_bytes_source=size_bytes_source,
            last_modified_source=last_modified_source,
            landing_key=landing_key,
            ingest_date=ingest_date,
            status=STATUS_FAILED,
            error=None,
            started_at=_now(),
        )
        with self.engine.begin() as conn:
            run_id = conn.execute(stmt).inserted_primary_key[0]
        logger.debug("manifest start id=%s dataset=%s resource_id=%s", run_id, dataset, resource_id)
        return int(run_id)

    def finish_ok(
        self,
        run_id: int,
        *,
        sha256: str | None,
        size_bytes_landed: int | None,
        landing_key: str | None,
        status: str = STATUS_OK,
    ) -> None:
        """Cierra la fila como `ok` o `unchanged`."""
        if status not in (STATUS_OK, STATUS_UNCHANGED):
            raise ValueError(f"status invalido para finish_ok: {status}")
        self._finish(
            run_id,
            status=status,
            sha256=sha256,
            size_bytes_landed=size_bytes_landed,
            landing_key=landing_key,
            error=None,
        )

    def finish_failed(self, run_id: int, error: str) -> None:
        """Cierra la fila como `failed` con el mensaje de error truncado."""
        self._finish(
            run_id,
            status=STATUS_FAILED,
            sha256=None,
            size_bytes_landed=None,
            landing_key=None,
            error=error[:2000],
        )

    def _finish(
        self,
        run_id: int,
        *,
        status: str,
        sha256: str | None,
        size_bytes_landed: int | None,
        landing_key: str | None,
        error: str | None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "sha256": sha256,
            "size_bytes_landed": size_bytes_landed,
            "error": error,
            "finished_at": _now(),
        }
        if landing_key is not None:
            values["landing_key"] = landing_key
        stmt = update(ingestion_manifest).where(ingestion_manifest.c.id == run_id).values(**values)
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def recent(self, dataset: str, limit: int = 20) -> list[dict[str, Any]]:
        """Ultimas filas del manifiesto para un dataset."""
        stmt = (
            select(ingestion_manifest)
            .where(ingestion_manifest.c.dataset == dataset)
            .order_by(desc(ingestion_manifest.c.id))
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]


__all__ = [
    "STATUS_FAILED",
    "STATUS_OK",
    "STATUS_UNCHANGED",
    "Manifest",
    "ingestion_manifest",
    "metadata",
]
