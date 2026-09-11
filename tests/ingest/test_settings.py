"""De donde sale el DSN de Postgres: del entorno o de SSM."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipelines.ingest import settings as settings_module

PARAMETRO = "/oil-lakehouse/dev/postgres_dsn"
DSN_EN_SSM = "postgresql://usuario:password@ep-ejemplo.neon.tech/lakehouse"


@pytest.fixture
def sin_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Devuelve un env_file inexistente y limpia las variables que decide cada test.

    El `.env` de la raiz existe en la maquina del que desarrolla: si no se pasa un
    env_file propio, el test pasaria o fallaria segun esa configuracion local.
    """
    for nombre in ("POSTGRES_DSN", "POSTGRES_DSN_SSM_PARAMETER", "S3_REGION"):
        monkeypatch.delenv(nombre, raising=False)
    return tmp_path / "no-existe.env"


def test_el_dsn_se_resuelve_por_ssm(monkeypatch: pytest.MonkeyPatch, sin_entorno: Path) -> None:
    llamadas = []

    def parameter_value(nombre: str, region: str) -> str:
        llamadas.append((nombre, region))
        return DSN_EN_SSM

    monkeypatch.setenv("POSTGRES_DSN_SSM_PARAMETER", PARAMETRO)
    monkeypatch.setattr(settings_module, "parameter_value", parameter_value)

    settings = settings_module.load_settings(env_file=sin_entorno)

    assert settings.postgres_dsn == DSN_EN_SSM
    assert llamadas == [(PARAMETRO, "us-east-1")]


def test_un_dsn_explicito_gana_sobre_ssm(
    monkeypatch: pytest.MonkeyPatch, sin_entorno: Path
) -> None:
    """Con el DSN en el entorno no se consulta SSM: es el modo de correr desde el host."""

    def no_deberia_llamarse(nombre: str, region: str) -> str:  # pragma: no cover
        raise AssertionError("no hay que ir a SSM si el DSN ya vino en el entorno")

    monkeypatch.setenv("POSTGRES_DSN", "postgresql://directo/lakehouse")
    monkeypatch.setenv("POSTGRES_DSN_SSM_PARAMETER", PARAMETRO)
    monkeypatch.setattr(settings_module, "parameter_value", no_deberia_llamarse)

    settings = settings_module.load_settings(env_file=sin_entorno)

    assert settings.postgres_dsn == "postgresql://directo/lakehouse"
