"""CLI de la ingesta (`ingest list|run|manifest`)."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from pipelines.ingest.ckan import CkanClient, build_session
from pipelines.ingest.manifest import Manifest
from pipelines.ingest.registry import DatasetSpec, get_dataset, load_registry
from pipelines.ingest.runner import discover, run
from pipelines.ingest.settings import Settings, load_settings
from pipelines.ingest.storage import LandingStorage

logger = logging.getLogger("pipelines.ingest")

app = typer.Typer(add_completion=False, help="Ingesta de fuentes publicas hacia landing.")

DatasetOpt = Annotated[str, typer.Option("--dataset", "-d", help="Nombre en datasets.yaml")]
OnlyOpt = Annotated[str | None, typer.Option("--only", help="Regex sobre el nombre del recurso")]


def _configure_logging(verbose: bool = False) -> None:
    """Logging a stderr, formato fijo, sin prints."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _context(dataset: str) -> tuple[DatasetSpec, Settings]:
    try:
        spec = get_dataset(dataset)
    except KeyError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=2) from None
    return spec, load_settings()


def _ckan_client(spec: DatasetSpec, settings: Settings, session) -> CkanClient | None:
    if spec.source_type != "ckan":
        return None
    return CkanClient(settings.ckan_base_url, session=session)


def _mb(value: int | None) -> str:
    return "-" if value is None else f"{value / 1_048_576:.1f} MB"


@app.command("datasets")
def list_datasets() -> None:
    """Lista los datasets del registro."""
    _configure_logging()
    for name, spec in sorted(load_registry().items()):
        logger.info(
            "dataset=%s source_type=%s landing_prefix=%s",
            name,
            spec.source_type,
            spec.landing_prefix,
        )


@app.command("list")
def list_resources(
    dataset: DatasetOpt,
    only: OnlyOpt = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Muestra los recursos de un dataset y si estan al dia segun el manifiesto."""
    _configure_logging(verbose)
    spec, settings = _context(dataset)
    session = build_session()
    ckan = _ckan_client(spec, settings, session)
    resources = discover(spec, ckan=ckan, session=session, only=only)
    manifest = Manifest(settings.postgres_dsn)
    for resource in resources:
        previous = manifest.latest_ok(spec.name, resource.id)
        up_to_date = bool(
            previous
            and resource.size is not None
            and previous.get("size_bytes_source") == resource.size
            and previous.get("last_modified_source") == resource.last_modified
        )
        logger.info(
            "recurso id=%s nombre=%r formato=%s tamaño=%s modificado=%s al_dia=%s",
            resource.id,
            resource.name,
            resource.format,
            _mb(resource.size),
            resource.last_modified,
            "si" if up_to_date else "no",
        )
    logger.info("dataset=%s recursos=%d", spec.name, len(resources))


@app.command("run")
def run_ingest(
    dataset: DatasetOpt,
    only: OnlyOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Solo lista lo que haria")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Ingesta un dataset a landing y registra el resultado en el manifiesto."""
    _configure_logging(verbose)
    spec, settings = _context(dataset)
    session = build_session()
    summary = run(
        spec,
        manifest=Manifest(settings.postgres_dsn),
        storage=LandingStorage.from_settings(settings),
        ckan=_ckan_client(spec, settings, session),
        session=session,
        only=only,
        dry_run=dry_run,
    )
    for item in summary.items:
        logger.info(
            "resultado recurso=%r status=%s key=%s bytes=%s descargado=%s error=%s",
            item.resource_name,
            item.status,
            item.landing_key,
            item.size_bytes,
            item.downloaded,
            item.error,
        )
    logger.info(
        "resumen dataset=%s ok=%d unchanged=%d failed=%d bytes_descargados=%d",
        summary.dataset,
        summary.ok,
        summary.unchanged,
        summary.failed,
        summary.downloaded_bytes,
    )
    raise typer.Exit(code=summary.exit_code)


@app.command("manifest")
def show_manifest(
    dataset: DatasetOpt,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
) -> None:
    """Ultimas filas del manifiesto para un dataset."""
    _configure_logging()
    spec, settings = _context(dataset)
    rows = Manifest(settings.postgres_dsn).recent(spec.name, limit=limit)
    for row in rows:
        logger.info(
            "id=%s status=%s recurso=%r sha256=%s bytes=%s key=%s ingest_date=%s error=%s",
            row["id"],
            row["status"],
            row["resource_name"],
            (row["sha256"] or "")[:12],
            row["size_bytes_landed"],
            row["landing_key"],
            row["ingest_date"],
            row["error"],
        )
    logger.info("dataset=%s filas=%d", spec.name, len(rows))


if __name__ == "__main__":  # pragma: no cover
    app()
