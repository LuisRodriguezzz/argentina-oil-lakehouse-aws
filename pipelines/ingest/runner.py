"""Orquesta una corrida de ingesta: descubrir recursos, decidir, descargar y registrar."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import requests

from pipelines.ingest.ckan import DEFAULT_TIMEOUT, CkanClient, Resource, build_session, force_http
from pipelines.ingest.manifest import STATUS_FAILED, STATUS_OK, STATUS_UNCHANGED, Manifest
from pipelines.ingest.registry import DatasetSpec
from pipelines.ingest.storage import LandingStorage, stable_url_id

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT = (10, 300)


@dataclass
class RunItem:
    """Resultado de un recurso dentro de la corrida."""

    resource_id: str
    resource_name: str
    status: str
    landing_key: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    downloaded: bool = False
    error: str | None = None


@dataclass
class RunSummary:
    """Contadores y detalle de una corrida."""

    dataset: str
    dry_run: bool = False
    items: list[RunItem] = field(default_factory=list)

    @property
    def ok(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_OK)

    @property
    def unchanged(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_UNCHANGED)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i.status == STATUS_FAILED)

    @property
    def downloaded_bytes(self) -> int:
        return sum(i.size_bytes or 0 for i in self.items if i.downloaded)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


# --- descubrimiento -------------------------------------------------------


def head_metadata(session: requests.Session, url: str) -> tuple[int | None, str | None]:
    """Tamaño y Last-Modified via HEAD. Si falla devuelve (None, None) y el GET reporta el error."""
    try:
        response = session.head(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.warning("head fallo url=%s error=%s", url, exc)
        return None, None
    if not response.ok:
        logger.warning("head url=%s status=%s", url, response.status_code)
        return None, None
    length = response.headers.get("Content-Length")
    size = int(length) if length and length.isdigit() else None
    return size, response.headers.get("Last-Modified")


def http_file_resources(spec: DatasetSpec, session: requests.Session) -> list[Resource]:
    """Un Resource por año; sin id de portal, se usa un hash estable de la URL."""
    resources = []
    for _year, url in spec.urls():
        size, last_modified = head_metadata(session, url)
        filename = url.rsplit("/", 1)[-1]
        resources.append(
            Resource(
                id=stable_url_id(url),
                name=filename,
                url=url,
                format=filename.rsplit(".", 1)[-1].upper() if "." in filename else "",
                size=size,
                last_modified=last_modified,
                datastore_active=False,
            )
        )
    return resources


def discover(
    spec: DatasetSpec,
    ckan: CkanClient | None = None,
    session: requests.Session | None = None,
    only: str | None = None,
) -> list[Resource]:
    """Recursos de la fuente tras include/exclude, `--only` y deduplicacion por resource_id."""
    if spec.source_type == "ckan":
        if ckan is None:
            raise ValueError("source_type ckan requiere un CkanClient")
        candidates = ckan.package_show(spec.ckan_package_id or "")
    else:
        candidates = http_file_resources(spec, session or build_session())

    only_pattern = re.compile(only, re.IGNORECASE) if only else None
    # dict por id: el portal publica 2024 dos veces con ids distintos y nombres iguales.
    selected: dict[str, Resource] = {}
    for resource in candidates:
        if not spec.matches(resource.name, resource.format):
            continue
        if only_pattern and not only_pattern.search(resource.name):
            continue
        if not resource.url:
            logger.warning("recurso sin url id=%s nombre=%s", resource.id, resource.name)
            continue
        selected.setdefault(resource.id, resource)
    logger.info(
        "dataset=%s candidatos=%d seleccionados=%d", spec.name, len(candidates), len(selected)
    )
    return list(selected.values())


# --- decision e ingesta de un recurso -------------------------------------


def is_unchanged_by_metadata(previous: dict[str, Any] | None, resource: Resource) -> bool:
    """True si tamaño y last_modified de origen coinciden con la ultima corrida ok."""
    if not previous or resource.size is None or resource.last_modified is None:
        return False
    return (
        previous["size_bytes_source"] == resource.size
        and previous["last_modified_source"] == resource.last_modified
    )


def stream_download(session: requests.Session, url: str) -> Iterator[bytes]:
    """GET en streaming: el contenido pasa a landing sin tocar el disco local."""
    with session.get(
        force_http(url), stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True
    ) as response:
        response.raise_for_status()
        yield from response.iter_content(chunk_size=DOWNLOAD_CHUNK)


def _open_row(
    manifest: Manifest,
    spec: DatasetSpec,
    resource: Resource,
    ingest_date: date,
    landing_key: str | None,
) -> int:
    """Abre la fila del manifiesto para este intento."""
    return manifest.start(
        dataset=spec.name,
        source_type=spec.source_type,
        resource_id=resource.id,
        resource_name=resource.name,
        url=resource.url,
        size_bytes_source=resource.size,
        last_modified_source=resource.last_modified,
        ingest_date=ingest_date,
        landing_key=landing_key,
    )


def _skip_download(
    manifest: Manifest,
    spec: DatasetSpec,
    resource: Resource,
    previous: dict[str, Any],
    ingest_date: date,
) -> RunItem:
    """Registra `unchanged` copiando el resultado anterior, sin bajar el archivo."""
    run_id = _open_row(manifest, spec, resource, ingest_date, previous["landing_key"])
    manifest.finish_ok(
        run_id,
        sha256=previous["sha256"],
        size_bytes_landed=previous["size_bytes_landed"],
        landing_key=previous["landing_key"],
        status=STATUS_UNCHANGED,
    )
    logger.info("unchanged por metadata dataset=%s recurso=%s", spec.name, resource.name)
    return RunItem(
        resource_id=resource.id,
        resource_name=resource.name,
        status=STATUS_UNCHANGED,
        landing_key=previous["landing_key"],
        size_bytes=previous["size_bytes_landed"],
        sha256=previous["sha256"],
    )


def _download(
    manifest: Manifest,
    storage: LandingStorage,
    session: requests.Session,
    spec: DatasetSpec,
    resource: Resource,
    previous: dict[str, Any] | None,
    ingest_date: date,
) -> RunItem:
    """Baja el recurso a landing y decide entre `ok` y `unchanged` comparando el hash."""
    key = storage.key_for(
        spec.landing_prefix, resource.id, resource.filename or resource.name, ingest_date
    )
    run_id = _open_row(manifest, spec, resource, ingest_date, key)
    logger.info(
        "descargando dataset=%s recurso=%s bytes_origen=%s", spec.name, resource.name, resource.size
    )
    try:
        result = storage.upload_stream(key, stream_download(session, resource.url))
    except Exception as exc:
        logger.error("fallo dataset=%s recurso=%s error=%s", spec.name, resource.name, exc)
        manifest.finish_failed(run_id, f"{type(exc).__name__}: {exc}")
        return RunItem(
            resource_id=resource.id,
            resource_name=resource.name,
            status=STATUS_FAILED,
            landing_key=key,
            error=f"{type(exc).__name__}: {exc}",
        )
    # Mismo contenido con otra fecha de publicacion: se registra unchanged.
    same_hash = previous is not None and previous["sha256"] == result.sha256
    status = STATUS_UNCHANGED if same_hash else STATUS_OK
    manifest.finish_ok(
        run_id,
        sha256=result.sha256,
        size_bytes_landed=result.size_bytes,
        landing_key=result.key,
        status=status,
    )
    logger.info(
        "%s dataset=%s recurso=%s key=%s bytes=%d",
        status,
        spec.name,
        resource.name,
        result.key,
        result.size_bytes,
    )
    return RunItem(
        resource_id=resource.id,
        resource_name=resource.name,
        status=status,
        landing_key=result.key,
        size_bytes=result.size_bytes,
        sha256=result.sha256,
        downloaded=True,
    )


def process_resource(
    spec: DatasetSpec,
    resource: Resource,
    manifest: Manifest,
    storage: LandingStorage,
    session: requests.Session,
    ingest_date: date,
) -> RunItem:
    """Ingesta un recurso. Nunca lanza: un recurso roto no corta la corrida."""
    try:
        previous = manifest.latest_ok(spec.name, resource.id)
        if is_unchanged_by_metadata(previous, resource):
            return _skip_download(manifest, spec, resource, previous, ingest_date)
        return _download(manifest, storage, session, spec, resource, previous, ingest_date)
    except Exception as exc:  # errores fuera de la descarga (manifiesto, key, etc.)
        logger.error("fallo dataset=%s recurso=%s error=%s", spec.name, resource.name, exc)
        return RunItem(
            resource_id=resource.id,
            resource_name=resource.name,
            status=STATUS_FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )


# --- corrida completa -----------------------------------------------------


def _dry_run_item(
    spec: DatasetSpec,
    resource: Resource,
    manifest: Manifest,
    storage: LandingStorage,
    ingest_date: date,
) -> RunItem:
    """Que haria la corrida con este recurso, sin tocar red ni manifiesto."""
    previous = manifest.latest_ok(spec.name, resource.id)
    status = STATUS_UNCHANGED if is_unchanged_by_metadata(previous, resource) else STATUS_OK
    key = storage.key_for(
        spec.landing_prefix, resource.id, resource.filename or resource.name, ingest_date
    )
    logger.info(
        "[dry-run] dataset=%s recurso=%s accion=%s key=%s bytes_origen=%s",
        spec.name,
        resource.name,
        status,
        key,
        resource.size,
    )
    return RunItem(
        resource_id=resource.id,
        resource_name=resource.name,
        status=status,
        landing_key=key,
        size_bytes=resource.size,
    )


def run(
    spec: DatasetSpec,
    manifest: Manifest,
    storage: LandingStorage,
    ckan: CkanClient | None = None,
    session: requests.Session | None = None,
    only: str | None = None,
    dry_run: bool = False,
    ingest_date: date | None = None,
) -> RunSummary:
    """Corre la ingesta completa de un dataset y devuelve el resumen."""
    http = session or build_session()
    day = ingest_date or datetime.now().date()
    summary = RunSummary(dataset=spec.name, dry_run=dry_run)

    for resource in discover(spec, ckan=ckan, session=http, only=only):
        if dry_run:
            summary.items.append(_dry_run_item(spec, resource, manifest, storage, day))
        else:
            summary.items.append(process_resource(spec, resource, manifest, storage, http, day))

    logger.info(
        "corrida dataset=%s ok=%d unchanged=%d failed=%d bytes_descargados=%d dry_run=%s",
        spec.name,
        summary.ok,
        summary.unchanged,
        summary.failed,
        summary.downloaded_bytes,
        dry_run,
    )
    return summary
