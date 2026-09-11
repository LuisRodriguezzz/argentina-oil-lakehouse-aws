"""La subida en streaming reconstruye el objeto y su sha256 coincide con hashlib."""

from __future__ import annotations

import hashlib
import os
from datetime import date

from pipelines.ingest.storage import (
    LandingStorage,
    build_key,
    sanitize_filename,
    stable_url_id,
)

from .conftest import BUCKET


def test_upload_stream_reconstruye_el_objeto_y_calcula_sha256(storage, s3_client):
    payload = os.urandom(12 * 1024 * 1024)  # fuerza mas de una parte de 5 MB
    chunks = [payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)]

    result = storage.upload_stream("energia/test/objeto.bin", iter(chunks))

    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size_bytes == len(payload)
    body = s3_client.get_object(Bucket=BUCKET, Key=result.key)["Body"].read()
    assert body == payload
    assert storage.object_size(result.key) == len(payload)


def test_upload_stream_soporta_contenido_vacio(storage, s3_client):
    result = storage.upload_stream("energia/test/vacio.bin", iter([]))
    assert result.size_bytes == 0
    assert result.sha256 == hashlib.sha256(b"").hexdigest()
    assert s3_client.get_object(Bucket=BUCKET, Key=result.key)["Body"].read() == b""


def test_upload_stream_aborta_si_el_iterador_falla(storage):
    def roto():
        yield b"a" * 1024
        raise OSError("conexion cortada")

    try:
        storage.upload_stream("energia/test/roto.bin", roto())
    except OSError:
        pass
    else:  # pragma: no cover
        raise AssertionError("deberia propagar el error")
    assert storage.object_size("energia/test/roto.bin") is None


def test_build_key_y_saneo_de_nombre():
    key = build_key(
        "energia/produccion_pozo",
        "0a352dee-8b4e-4e95-b01e-5b8082ce22ac",
        "Producción de Pozos 2024.csv",
        date(2026, 9, 5),
    )
    assert key == (
        "energia/produccion_pozo/resource_id=0a352dee-8b4e-4e95-b01e-5b8082ce22ac"
        "/ingest_date=2026-09-05/produccion-de-pozos-2024.csv"
    )
    assert sanitize_filename("áé í.CSV") == "ae-i.csv"
    assert sanitize_filename("///") == "archivo"


def test_stable_url_id_es_determinista():
    url = "http://www.energia.gob.ar/reservas_al_31-12-2024.zip"
    assert stable_url_id(url) == stable_url_id(url)
    assert len(stable_url_id(url)) == 16
    assert stable_url_id(url) != stable_url_id(url + "x")


def test_key_for_sin_prefijo_es_la_key_del_dataset(storage):
    # Sin prefijo la key arranca directo con el prefijo del dataset.
    key = storage.key_for("energia/produccion_pozo", "r1", "x.csv", date(2026, 9, 5))
    assert key == "energia/produccion_pozo/resource_id=r1/ingest_date=2026-09-05/x.csv"


def test_key_for_antepone_el_prefijo_del_bucket(s3_client):
    # En AWS un solo bucket guarda landing/, warehouse/ y artifacts/.
    storage = LandingStorage(
        region="us-east-1",
        bucket="lakehouse-dev-123",
        client=s3_client,
        prefix="landing",
    )
    key = storage.key_for("energia/produccion_pozo", "r1", "x.csv", date(2026, 9, 5))
    assert key == "landing/energia/produccion_pozo/resource_id=r1/ingest_date=2026-09-05/x.csv"
