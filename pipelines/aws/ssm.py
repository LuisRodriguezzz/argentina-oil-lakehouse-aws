"""Lectura de SSM Parameter Store.

Los secretos (la cadena de conexión a Postgres) no viajan en los argumentos de un job de
Glue: quedarían visibles en la consola y en `get-job-runs`. El job recibe el *nombre* del
parámetro y lo resuelve acá con el rol que ya tiene.
"""

from __future__ import annotations

import boto3


def parameter_value(name: str, region: str | None = None) -> str:
    """Valor descifrado del parámetro `name`."""
    # `region_name=None` es lo mismo que no pasarlo: boto3 cae en la región del entorno.
    client = boto3.client("ssm", region_name=region)
    return client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
