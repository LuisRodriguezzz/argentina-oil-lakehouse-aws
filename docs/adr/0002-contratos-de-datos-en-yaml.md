# ADR 0002 — El contrato de datos vive en YAML versionado

**Estado:** aceptada · 2026-09-05

## Contexto

Silver tiene que tipar lo que bronze guardó como string y decidir qué filas son publicables.
Ese conocimiento (tipos, unicidad, rangos verificados en la semana 0: `tef` en [0, 744],
`prod_*` ≥ 0, `empresa` no nula) puede vivir en el código del job, en una herramienta de
calidad aparte, o en un archivo declarativo. Si vive en dos lugares, se desincronizan.

## Decisión

Un contrato por tabla silver en `pipelines/contracts/*.yaml`, versionado con el repo, que es
a la vez documentación y entrada del job. `silver_load.py --contract produccion_pozo` no
tiene ninguna regla propia: arma las expresiones de casteo y de validación desde el YAML.

Las validaciones son de dos tipos:

- **Duras** (columna del contrato ausente en bronze, nulo en una columna `nullable: false`,
  clave primaria duplicada después de deduplicar, más de 1 % de filas rechazadas): el job
  termina con código 1 y no escribe. Son señales de que el esquema de origen cambió o de que
  el contrato está mal; publicar igual sería propagar el error a gold.
- **Blandas** (`min`, `max`, `allowed_values`): son filas malas, no una fuente rota. La fila
  no entra a silver y el resto de la carga sigue.

Las filas rechazadas se guardan en `<tabla>_rejects` con el motivo y sus strings originales,
y cada corrida deja una fila en `lake.silver.dq_runs`.

## Consecuencias

- Agregar una columna o cambiar un rango es editar un YAML, y el diff del PR muestra el
  cambio de reglas de negocio sin leer código.
- Guardar los rechazos en vez de descartarlos: un `tef` negativo es un dato de la fuente que
  hay que poder mostrar y contar, no un fantasma en un log que rota. Con la cuarentena, la
  pregunta "cuántas filas se perdieron y por qué" se contesta con SQL.
- El umbral de 1 % es arbitrario y global; cuando alguna tabla lo necesite distinto, pasa a
  ser un campo del contrato.
- Los YAML no nombran el ambiente: dicen `lake.silver.fractura` y el sufijo se lo agrega el
  job (ADR 0005). Un contrato se lee solo, sin saber dónde se va a aplicar.
