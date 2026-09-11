"""Cliente minimo de CKAN para el portal de datos abiertos de Energia."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# El portal responde 301 de https a http; forzamos http para no pagar el redirect.
PORTAL_HOST_SUFFIX = "energia.gob.ar"
USER_AGENT = "argentina-oil-lakehouse-ingest/0.1 (+https://github.com/)"
DEFAULT_TIMEOUT = (10, 120)


@dataclass(frozen=True)
class Resource:
    """Recurso de un paquete CKAN."""

    id: str
    name: str
    url: str
    format: str
    size: int | None
    last_modified: str | None

    @property
    def filename(self) -> str:
        """Nombre de archivo publicado en la URL de descarga."""
        return urlsplit(self.url).path.rsplit("/", 1)[-1]


def force_http(url: str, host_suffix: str = PORTAL_HOST_SUFFIX) -> str:
    """Baja a http:// las URLs del portal (https redirige 301 a http)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == host_suffix or host.endswith("." + host_suffix)):
        return urlunsplit(("http", parts.netloc, parts.path, parts.query, parts.fragment))
    return url


def build_session(
    total_retries: int = 4,
    backoff_factor: float = 1.0,
    pool_maxsize: int = 8,
) -> requests.Session:
    """Session con reintentos y backoff exponencial para 429/5xx."""
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=pool_maxsize)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def resource_from_dict(raw: dict[str, Any], host_suffix: str = PORTAL_HOST_SUFFIX) -> Resource:
    """Convierte el dict crudo de CKAN en Resource, normalizando tipos."""
    size = raw.get("size")
    if isinstance(size, str):
        size = int(size) if size.strip().isdigit() else None
    # Algunos recursos traen last_modified nulo; created es el mejor sustituto.
    last_modified = raw.get("last_modified") or raw.get("created")
    return Resource(
        id=str(raw["id"]),
        name=(raw.get("name") or "").strip(),
        url=force_http(str(raw.get("url") or ""), host_suffix),
        format=(raw.get("format") or "").strip().upper(),
        size=size if isinstance(size, int) else None,
        last_modified=str(last_modified) if last_modified else None,
    )


class CkanClient:
    """Acceso de solo lectura a la API action de CKAN."""

    def __init__(
        self,
        base_url: str,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        host_suffix: str = PORTAL_HOST_SUFFIX,
    ) -> None:
        self.host_suffix = host_suffix
        self.base_url = force_http(base_url.rstrip("/"), host_suffix)
        self.session = session or build_session()
        self.timeout = timeout

    def package_show(self, package_id: str) -> list[Resource]:
        """Recursos de un paquete; lanza si CKAN responde error."""
        url = f"{self.base_url}/api/3/action/package_show"
        logger.info("ckan package_show package_id=%s", package_id)
        response = self.session.get(
            url, params={"id": package_id}, timeout=self.timeout, allow_redirects=True
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", False):
            raise RuntimeError(f"CKAN respondio error para {package_id}: {payload.get('error')}")
        raw_resources = payload.get("result", {}).get("resources", []) or []
        resources = [resource_from_dict(r, self.host_suffix) for r in raw_resources]
        logger.info("ckan package_id=%s recursos=%d", package_id, len(resources))
        return resources
