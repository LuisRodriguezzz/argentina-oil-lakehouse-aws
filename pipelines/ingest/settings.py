"""Configuracion de la ingesta: variables de entorno (nunca hardcodeada)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from pipelines.aws.ssm import parameter_value

# pipelines/ingest/settings.py -> raiz del repo. El `.env` es opcional y sirve para correr
# la ingesta desde el host; en Glue la configuracion llega por argumentos del job.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Variables de entorno usadas por la ingesta."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # No hay claves ni endpoint de S3: boto3 usa las credenciales del rol del job de Glue,
    # o las de `~/.aws` cuando la ingesta corre desde el host.
    s3_region: str = "us-east-1"
    # Sin default: el bucket lo crea Terraform y su nombre lleva el id de la cuenta.
    s3_landing_bucket: str = ""
    # Prefijo dentro del bucket, porque el mismo bucket guarda warehouse/ y artifacts/.
    s3_landing_prefix: str = "landing"
    postgres_dsn: str = ""
    # Alternativa al DSN en claro: nombre del parametro SecureString de SSM que lo guarda.
    postgres_dsn_ssm_parameter: str = ""
    ckan_base_url: str = "http://datos.energia.gob.ar"


def load_settings(env_file: Path | str | None = None) -> Settings:
    """Construye Settings desde un .env explicito o el `.env` opcional de la raiz."""
    path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE
    settings = Settings(_env_file=path if path.exists() else None)  # type: ignore[call-arg]
    if not settings.postgres_dsn and settings.postgres_dsn_ssm_parameter:
        settings.postgres_dsn = parameter_value(
            settings.postgres_dsn_ssm_parameter, settings.s3_region
        )
    return settings
