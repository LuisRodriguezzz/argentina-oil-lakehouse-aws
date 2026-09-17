# Fuente: datos de fractura de pozos (Adjunto IV)

Dataset `datos-de-fractura-de-pozos-adjunto-iv` del portal de la Secretaría de Energía (el
Adjunto IV es el formulario con que las operadoras declaran cada operación de fractura). Un
CSV único que se reemplaza entero todos los días, con **una fila por operación de fractura
declarada**: cuánta arena y agua se bombeó, cuántas etapas, qué presión y qué equipo. Es el
lado "cómo se estimuló el pozo" del que `produccion_pozo` cuenta el resultado; se cruzan por
`idpozo`.

El recurso trae la advertencia **"datos preliminares sujetos a revisión"**: las filas cambian
de un día para el otro, por eso la máquina de estados es diaria y bronze recarga el recurso
completo cuando cambia el sha256.

Medido sobre `lake.bronze.fractura` el 2026-09-05: **4.890 filas, 30 columnas de datos**
(más las 6 de linaje que agrega bronze), un solo recurso, `data_origin = real`.

## Columnas

| Columna | Tipo | Significado |
| --- | --- | --- |
| `id_base_fractura_adjiv` | bigint | Id de la declaración en la base del Adjunto IV. **Clave primaria** |
| `idpozo` | bigint | Pozo fracturado; cruza con `produccion_pozo.idpozo` |
| `sigla` | string | Sigla del pozo, el identificador legible de la industria |
| `cuenca` | string | Cuenca sedimentaria: NEUQUINA (4.087), GOLFO SAN JORGE (589), AUSTRAL (214) |
| `areapermisoconcesion` | string | Área de permiso o concesión (106 valores) |
| `yacimiento` | string | Yacimiento (141 valores) |
| `formacion_productiva` | string | Formación estimulada; llega en minúsculas (`vaca muerta`, 3.001 filas) |
| `tipo_reservorio` | string | CONVENCIONAL / NO CONVENCIONAL / NO DISCRIMINADO |
| `subtipo_reservorio` | string | SHALE o TIGHT; solo aplica al no convencional |
| `longitud_rama_horizontal_m` | double | Longitud de la rama horizontal en metros; 0 en pozos verticales |
| `cantidad_fracturas` | int | Etapas de fractura bombeadas |
| `tipo_terminacion` | string | Tapón disparo, Punzado, Camisas deslizables, Jetteo, Camisas y punzados |
| `arena_bombeada_nacional_tn` | double | Agente de sostén de origen nacional, en toneladas |
| `arena_bombeada_importada_tn` | double | Agente de sostén importado, en toneladas |
| `agua_inyectada_m3` | double | Agua bombeada en el tratamiento, en m3 |
| `co2_inyectado_m3` | double | CO2 inyectado en el tratamiento, en m3 |
| `presion_maxima_psi` | double | Presión máxima de tratamiento, en psi |
| `potencia_equipos_fractura_hp` | double | Potencia hidráulica del set de bombeo, en HP |
| `fecha_inicio_fractura` | date | Inicio del tratamiento; es la fecha de referencia del registro |
| `fecha_fin_fractura` | date | Fin del tratamiento |
| `fecha_data` | timestamp | Momento en que la fila entró o se corrigió en el sistema |
| `anio_if` / `mes_if` | int | Año y mes de `fecha_inicio_fractura`, precalculados |
| `anio_ff` / `mes_ff` | int | Año y mes de `fecha_fin_fractura`, precalculados |
| `anio_carga` / `mes_carga` | int | Año y mes de la carga; van de 2019 a 2026 |
| `empresa_informante` | string | Razón social de la operadora (23 empresas; YPF S.A. declara 2.225 filas) |
| `anio` / `mes` | int | Duplicados exactos de `anio_if` / `mes_if` (0 diferencias). `anio` es la partición |

## Clave

`id_base_fractura_adjiv` es único: 4.890 valores distintos sobre 4.890 filas. Se verificó que
`idpozo` **no** alcanza (4.646 claves) y que `idpozo + fecha_inicio_fractura` **tampoco**
(4.860 claves): hay pozos con varias declaraciones el mismo día, hasta 6 para el pozo 159341
el 2018-05-28. Casi siempre son partes de un mismo trabajo declaradas por separado (se suman);
unas pocas son filas repetidas exactas, con las mismas fechas, rama, etapas y arena.

Medido el 2026-09-16 sobre `gold.fact_fractura`, por pozo: 4.453 tienen una sola declaración
y 182 tienen más de una. De esos, 59 tienen un trabajo partido en varias filas (menos de 45
días entre una y otra) y 129 tienen más de un trabajo (una re-fractura más de 45 días
después); algunos están en las dos cuentas. `mart_pozo_completacion_produccion` consolida las
filas de un mismo trabajo y, para emparejar con la producción temprana, elige el trabajo
anterior a la primera producción del pozo.

`fecha_data` es el campo de actualización, así que es el `dedupe_by` del contrato: si una
declaración vuelve corregida gana la de `fecha_data` más alta.

## Rarezas medidas

- **Nulos**: solo tres columnas los tienen. `subtipo_reservorio` 974 (933 son convencionales,
  donde no aplica), `tipo_reservorio` 35 y `potencia_equipos_fractura_hp` 51. Las otras 27
  columnas no tienen ninguno.
- **Fechas invertidas**: 41 filas tienen `fecha_fin_fractura < fecha_inicio_fractura`. Tres de
  ellas (ids 5489-5491) declaran inicio en octubre, noviembre y diciembre de 2026 con fin el
  2026-05-28: parecen día y mes intercambiados en la carga. No se rechazan porque el contrato
  valida columna por columna, no relaciones entre columnas.
- **Presiones imposibles**: 12 filas superan los 20.000 psi, con un máximo de 209.640. Una
  fractura real trabaja entre 5.000 y 15.000 psi; arriba de eso es un error de unidad. El
  contrato las manda a cuarentena (`max: 20000`), 0,25 % de las filas.
- **Potencia imposible**: una fila declara 232.159 HP contra 58.161 de la segunda más alta.
  Un set de bombeo grande ronda los 50.000 HP; el contrato corta en 100.000.
- **Longitud 0**: 2.054 filas, y 861 de ellas son convencionales. Es un pozo vertical, no un
  dato faltante: por eso el mínimo es 0 y no hay rechazo.
- **Arena importada**: dos filas declaran 80.652 y 77.056 tn contra 11.840 de la tercera. Se
  documenta pero no se rechaza: no hay un techo físico defendible para el agente de sostén.
- **Redundancia**: `anio`/`mes`, `anio_if`/`mes_if` y el año/mes de `fecha_inicio_fractura`
  coinciden en las 4.890 filas. Se conservan las tres porque el contrato no deriva columnas;
  `anio` es la que particiona.
