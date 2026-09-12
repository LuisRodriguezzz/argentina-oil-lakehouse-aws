# ADR 0004 — CI en GitHub Actions

**Estado:** aceptada · 2026-09-05

## Contexto

El repo se sube a GitHub como público. Un repo público tiene minutos ilimitados en GitHub
Actions (los privados tienen cuota mensual), así que no hay motivo para buscar otro runner.

La pregunta real es hasta dónde llega el CI. Verificar de verdad el pipeline pide una cuenta de
AWS: los jobs de Spark corren en Glue, el catálogo es el Glue Data Catalog y gold la ejecuta
Athena. Poner credenciales en un repo público no es una opción, y aun con OIDC cada push
gastaría presupuesto y rompería el costo cero en reposo (ADR 0001).

## Decisión

Un solo workflow `ci` en `push` y `pull_request` sobre `main`, con tres jobs, y **ninguno toca
AWS**:

- `lint-y-tests`: `uv sync --all-groups`, `ruff check`, `ruff format --check` y `pytest`. Los
  tests corren contra Moto y SQLite (ver `tests/ingest/conftest.py`), no contra servicios
  reales, y las funciones puras de bronze y silver se verifican comparando las expresiones SQL
  que generan, sin levantar Spark. Cierra construyendo el wheel y comprobando que se llame como
  dice `var.wheel_name` en Terraform: si sube la versión del proyecto y nadie toca el tfvars, los
  jobs de Glue apuntan a un archivo que no está en `artifacts/` y se descubre recién cuando falla
  una corrida.
- `dbt`: `dbt parse`, que compila el proyecto entero sin conectarse a Athena y atrapa un `ref()`
  a un modelo que no existe, un `.yml` que documenta un modelo que ya no está o una macro mal
  escrita. El `build` y los 82 tests necesitan la cuenta y corren en el job de gold (ADR 0003).
- `terraform`: `terraform fmt -check -recursive`, `terraform init -backend=false` y
  `terraform validate`, sobre `infra/terraform/` y también sobre `infra/terraform/bootstrap/`,
  que es un directorio raíz aparte con su propio provider y su propio state. Sin credenciales y
  sin `plan` ni `apply`. Tampoco hace falta `-var-file`: `validate` no evalúa los valores de las
  variables, así que `environment` puede seguir sin default (ADR 0005).

Lo que el CI no valida: que los jobs de Glue corran de verdad contra Iceberg, que el contrato
aplicado sobre bronze produzca la tabla esperada, ni la ingesta contra las fuentes públicas
reales. Esa integración se verifica disparando la máquina de estados a mano.

`plan` y `apply` viven en un workflow aparte, `.github/workflows/deploy.yml`, que se autentica
con OIDC contra un rol por ambiente (ADR 0005). Están separados a propósito: el CI corre en cada
push de cualquier rama y el despliegue solo desde `main`.

## Consecuencias

- Un error de sintaxis en Terraform, un import roto o una regla de contrato mal armada se
  detectan en minutos, sin abrir la consola de AWS.
- El CI puede dar verde con un job de Glue roto en runtime (una tabla Iceberg inexistente, un
  argumento que Terraform dejó de pasar): esa clase de bug sigue dependiendo de la corrida real.
- El workflow es barato y rápido, y no consume presupuesto de la cuenta de AWS: se puede correr
  en cada push sin pensarlo.
