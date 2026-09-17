# Ingesta a landing

Baja las fuentes públicas del upstream argentino a la zona `landing/` del bucket del lakehouse,
en streaming, y registra cada intento en el manifiesto: la tabla `ingestion_manifest` de
Postgres, con archivo, fecha, tamaño, sha256 y resultado. No escribe nada en disco. El registro de fuentes es `datasets.yaml`: hoy son `produccion_pozo` (CKAN),
`fractura` (CKAN) y `reservas` (ZIP anual por URL).

## Cómo se invoca en AWS

Es un único job de Glue Python shell genérico, `ingest_landing`, al que cada máquina de estados
le pasa su `--dataset`. El wrapper es `pipelines/aws/ingest_job.py` y llama a `runner.run()`
directo, sin pasar por la CLI.

Desde el host, contra el mismo bucket, con la CLI:

```bash
uv run ingest datasets                                   # fuentes del registro
uv run ingest list --dataset produccion_pozo             # recursos y si están al día
uv run ingest run  --dataset produccion_pozo --only 2024 # ingesta filtrando por nombre
uv run ingest run  --dataset fractura --dry-run          # solo muestra qué haría
uv run ingest manifest --dataset produccion_pozo -n 20   # últimas filas del manifiesto
```

Configuración: variables de entorno, o el `.env` opcional de la raíz del repo (ver
`.env.example`); en los jobs las pone Terraform como argumentos. Las credenciales de AWS no
están ahí: boto3 usa el rol del job dentro de AWS y `~/.aws` desde el host. `run` devuelve
código de salida 1 si algún recurso falló; el resto de la corrida sigue igual.

## Decisiones

- **Registro declarativo** (`datasets.yaml`): agregar una fuente no requiere tocar código.
  Además de `include`/`exclude` (regex sobre el nombre) hay `formats`, porque el recurso
  "Capítulo IV - Pozos" (un catálogo de pozos) existe con el mismo nombre en CSV y en SHP.
- **Familia DDJJ**: el portal publica cada año de producción en dos CSV, el "normal" y el de
  "DDJJ abiertas y cerradas"; se ingesta el segundo, el único que sigue actualizándose
  ([comparación](../../docs/fuentes/comparacion-familias-produccion.md)). La deduplicación es
  por `resource_id`, no por nombre: el portal repite 2024 con dos ids.
- **Python 3.9 en Python shell**: el job de Glue más barato trae Python 3.9, así que el wrapper
  no pasa por la CLI (typer pide 3.10) y `manifest.py` no usa sintaxis ni stdlib posteriores.
  `ruff` lo verifica con una versión objetivo por archivo (`pyproject.toml`).
- **HTTP plano**: `https://datos.energia.gob.ar` redirige 301 a `http`. `force_http` baja el
  esquema para los hosts `*.energia.gob.ar` y así se evita el redirect en cada descarga.
- **Idempotencia en dos niveles**: si `size` y `last_modified` de origen coinciden con la
  última corrida `ok`, se registra `unchanged` sin descargar. Si cambiaron, se descarga y se
  compara el sha256: contenido igual también es `unchanged`. Solo contenido nuevo es `ok`.
- **Streaming con multipart de 8 MB**: el sha256 se calcula sobre los mismos bytes que se
  suben, sin buffer completo en RAM ni archivo temporal, aunque el recurso pese 300 MB. Es lo
  que permite que la ingesta entre en 1/16 de DPU, el job de Glue más barato que hay.
- **Fila pesimista**: `start()` inserta en estado `failed` y el cierre la promueve. Un proceso
  que muere a mitad de camino deja la evidencia en el manifiesto en vez de perderla.
- **`reservas` fuera de CKAN**: ZIP anual por URL. Como no hay id de portal, el `resource_id`
  es un hash corto y estable de la URL. Verificado por HEAD: solo 2020-2024 están publicados.
