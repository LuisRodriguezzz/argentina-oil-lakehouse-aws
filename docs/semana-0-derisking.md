# Bitácora de la semana 0: de-risking (2026-09-05)

Documento histórico. Son las pruebas ejecutadas contra las fuentes reales antes de escribir una
línea de infraestructura, tal como se anotaron ese día: todo lo de abajo se midió, no se leyó.
Las fichas por fuente en [`docs/fuentes/`](fuentes/) repiten y extienden estas mediciones sobre
las tablas ya cargadas, y son la referencia vigente si algún número difiere.

## Portal datos.energia.gob.ar

- Descarga directa por `http://` sin redirecciones: 320 MB en 17,5 s (18 MB/s). Confirma que la ingesta no necesita ningún truco de red ni un proceso largo para bajar un año entero.
- `package_show` del dataset de producción por pozo devuelve 53 recursos. Cada año existe en dos familias: la normal y la "DDJJ abiertas y cerradas". El año 2024 aparece dos veces con ids distintos (`43a09dce…` y `94d82d18…`) y mismo tamaño. Hay que elegir una familia y deduplicar por id de recurso, no por nombre.
- Volumen total de una familia completa 2006-2026: unos 7,3 GB en CSV. Recursos agregados útiles: No Convencional (145 MB), Capítulo IV Pozos (34 MB), padrón de primera producción (1,2 MB, 86.197 pozos), agrupado por yacimiento y formación (124 MB).

## CSV de producción 2024

| Métrica | Valor |
|---|---|
| Filas | 983.551 |
| Columnas | 39 (las 38 conocidas + `id`) |
| Pozos únicos | 82.379 |
| Empresas | 59 |
| Filas YPF S.A. | 471.757 (48,0 %) |
| Pozos YPF | 39.787 |
| Duplicados `idpozo+anio+mes` | 0 |
| Lectura con Polars | 0,6 s |

- Esquema: las columnas numéricas llegan con decimales ("0.000"), así que hay que forzar Float64; la inferencia automática falla.
- `tef` va de -0,01 a 720 con mediana 0: son horas efectivas de producción en el mes (720 = 30 días × 24 h). El valor negativo confirma que necesita test de rango.
- `vida_util` es 0 en todas las filas de 2024: columna vacía en este año; no usar como feature sin verificar otros años.
- YPF 2024 por tipo de recurso: no convencional tiene 26.148 filas pero 12,7 millones de m³ de petróleo; convencional tiene 445.609 filas y 7,4 millones. Vaca Muerta concentra el volumen en pocos pozos.
- Cuencas de YPF por filas: Golfo San Jorge 253.043, Neuquina 172.659, Cuyana 37.367, Austral 8.676.
- `tipoestado` dominante: Extracción Efectiva 321.124, Abandonado 224.200. Un tercio de las filas son pozos que no producen; bronze los conserva y silver filtra por estado solo donde corresponda.
- Encoding: UTF-8 con BOM; usar `encoding="utf8-lossy"` o quitar el BOM.

## XLSX de reservas 2024

- ZIP de 314 KB con un único XLSX de 416 KB y dos hojas: `fin de concesión` y `fin de vida util`.
- 1.243 filas × 29 columnas. Encabezado de 7 filas; rangos fusionados en filas 1-2 (título), 3 (Convencional / No convencional), 4 (Reservas / Recursos contingentes) y 5 (Comprobadas / Probables / Posibles). Fila 6 = PET/GAS, fila 7 = nombres y unidades (Mm3 / MMm3).
- Columnas de identificación en fila 7: `OPERADOR, CUENCA, PROVINCIA, CONCESIÓN O PERMISO, YACIMIENTO`.
- Filas de YPF S.A.: 383 de 1.236 (primer operador; siguen Venoil 107, PAE 93, Tecpetrol 73).

## Decisiones que salen de la semana 0

1. Ingesta por `http://` con `requests` y `allow_redirects=True`; nunca forzar HTTPS.
2. Bronze conserva todo tal cual (incluidos pozos abandonados); silver aplica esquema explícito con Float64 y filtra por `tipoestado` solo en los modelos que lo requieran.
3. Contrato de datos para producción: unicidad `idpozo+anio+mes`, `tef` en [0, 744], `prod_*` ≥ 0, `empresa` no nula.
4. ~~Elegir la familia "DDJJ abiertas y cerradas" o la normal después de comparar un año completo entre ambas (pendiente).~~
   **Resuelto**: se compararon los CSV completos de 2024 de ambas familias con Polars. DDJJ
   abiertas y cerradas trae 0 diferencias en las columnas de producción, inyección y estado de
   las 983.551 declaraciones que comparten (solo corrige metadata de catálogo en unos pocos
   pozos) y +159 declaraciones rectificadas que la normal no tiene; es además la única que la
   Secretaría sigue actualizando (según CKAN, la normal quedó congelada 5 meses antes que la
   última actualización de DDJJ). Queda elegida DDJJ abiertas y cerradas. Detalle completo en
   [`docs/fuentes/comparacion-familias-produccion.md`](fuentes/comparacion-familias-produccion.md)
   y ficha de la fuente en [`docs/fuentes/produccion_pozo.md`](fuentes/produccion_pozo.md).
