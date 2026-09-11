"""Registro declarativo de fuentes (datasets.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("datasets.yaml")
SOURCE_TYPES = ("ckan", "http_file")


@dataclass(frozen=True)
class DatasetSpec:
    """Una fuente del registro."""

    name: str
    source_type: str
    landing_prefix: str
    ckan_package_id: str | None = None
    url_template: str | None = None
    years: tuple[int, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"{self.name}: source_type invalido {self.source_type!r}")
        if self.source_type == "ckan" and not self.ckan_package_id:
            raise ValueError(f"{self.name}: source_type ckan requiere ckan_package_id")
        if self.source_type == "http_file":
            if not self.url_template or "{year}" not in self.url_template:
                raise ValueError(f"{self.name}: url_template debe contener {{year}}")
            if not self.years:
                raise ValueError(f"{self.name}: source_type http_file requiere years")
        for pattern in (*self.include, *self.exclude):
            re.compile(pattern)

    def matches(self, resource_name: str, resource_format: str | None = None) -> bool:
        """True si el recurso pasa include, exclude y la lista de formatos."""
        if self.formats:
            fmt = (resource_format or "").strip().upper()
            if fmt not in {f.upper() for f in self.formats}:
                return False
        if self.include and not any(_search(p, resource_name) for p in self.include):
            return False
        return not any(_search(p, resource_name) for p in self.exclude)

    def urls(self) -> list[tuple[int, str]]:
        """Pares (año, url) para fuentes http_file."""
        if self.source_type != "http_file" or not self.url_template:
            return []
        return [(y, self.url_template.format(year=y)) for y in self.years]


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _spec_from_dict(raw: dict[str, Any]) -> DatasetSpec:
    return DatasetSpec(
        name=raw["name"],
        source_type=raw["source_type"],
        landing_prefix=raw["landing_prefix"],
        ckan_package_id=raw.get("ckan_package_id"),
        url_template=raw.get("url_template"),
        years=tuple(raw.get("years") or ()),
        include=tuple(raw.get("include") or ()),
        exclude=tuple(raw.get("exclude") or ()),
        formats=tuple(raw.get("formats") or ()),
    )


def load_registry(path: Path | str | None = None) -> dict[str, DatasetSpec]:
    """Carga el registro completo indexado por nombre."""
    registry_path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("datasets") or []
    specs: dict[str, DatasetSpec] = {}
    for entry in entries:
        spec = _spec_from_dict(entry)
        if spec.name in specs:
            raise ValueError(f"dataset duplicado en el registro: {spec.name}")
        specs[spec.name] = spec
    return specs


def get_dataset(name: str, path: Path | str | None = None) -> DatasetSpec:
    """Devuelve una fuente por nombre; error claro si no existe."""
    specs = load_registry(path)
    try:
        return specs[name]
    except KeyError:
        raise KeyError(f"dataset {name!r} no esta en el registro: {sorted(specs)}") from None
