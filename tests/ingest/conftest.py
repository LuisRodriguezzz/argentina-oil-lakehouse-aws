"""Fixtures compartidas: registro de prueba, S3 en Moto y manifiesto en SQLite."""

from __future__ import annotations

import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from pipelines.ingest.manifest import Manifest
from pipelines.ingest.registry import load_registry
from pipelines.ingest.storage import LandingStorage

CKAN_BASE = "http://datos.energia.gob.ar"
BUCKET = "landing"

FIXTURE_YAML = """
datasets:
  - name: produccion_pozo
    source_type: ckan
    ckan_package_id: produccion-de-petroleo-y-gas-por-pozo
    landing_prefix: energia/produccion_pozo
    include:
      - "\\\\(DDJJ abiertas y cerradas\\\\)"
      - "No Convencional"
      - "^Capítulo IV - Pozos$"
    exclude:
      - "(?i)shape"
    formats: ["CSV"]

  - name: reservas
    source_type: http_file
    url_template: "http://www.energia.gob.ar/reservas_al_31-12-{year}.zip"
    years: [2024]
    landing_prefix: energia/reservas
"""


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    path = tmp_path / "datasets.yaml"
    path.write_text(FIXTURE_YAML, encoding="utf-8")
    return path


@pytest.fixture
def specs(registry_path: Path):
    return load_registry(registry_path)


@pytest.fixture
def aws_credentials():
    """Credenciales falsas para que boto3 no toque el entorno real."""
    saved = {
        k: os.environ.get(k)
        for k in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
            "AWS_ENDPOINT_URL",
        )
    }
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_DEFAULT_REGION="us-east-1",
    )
    os.environ.pop("AWS_ENDPOINT_URL", None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def s3_client(aws_credentials):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def storage(s3_client) -> LandingStorage:
    return LandingStorage(
        region="us-east-1",
        bucket=BUCKET,
        client=s3_client,
        part_size=5 * 1024 * 1024,
    )


@pytest.fixture
def manifest() -> Manifest:
    return Manifest("sqlite://")
