# ADR 0001 — Lakehouse serverless: Glue, Step Functions y Athena

**Estado:** aceptada · 2026-09-11

**Actualizada:** 2026-09-12 — el state de Terraform pasó de local a S3 (ADR 0005).

## Contexto

El pipeline entero —ingesta, bronze, silver sobre Iceberg y gold— tiene que correr en AWS con
un presupuesto de 5 USD y créditos de plan gratuito. La restricción no es técnica sino
económica: cualquier recurso que quede prendido —un NAT Gateway, una instancia de RDS, un
clúster de EMR, un entorno de MWAA— consume el presupuesto entero sin que nadie ejecute nada.

De ahí el principio que ordena todas las elecciones de abajo: **costo cero en reposo**. Si
nadie dispara una corrida, la cuenta no factura más que unos MB en S3. Todo lo que se elija
tiene que cobrarse por uso, no por existir.

## Decisión

**Spark en Glue y no en EMR.** Glue cobra por DPU-hora consumida y no deja nada corriendo
entre ejecuciones: los jobs de bronze y silver cuestan centavos por corrida. Un clúster EMR,
aun transitorio, factura las instancias mientras está levantado y agrega tiempo de arranque.
Glue 5.0 además trae los jars de Iceberg con `--datalake-formats iceberg`, así que no hay que
resolver dependencias de la JVM a mano. El catálogo es el Glue Data Catalog: las tablas
Iceberg viven en S3 y las ve cualquier motor de la cuenta.

**Athena como motor de consulta.** Lee las mismas tablas Iceberg del Glue Data Catalog que
escriben bronze y silver, no hay nada que levantar y se paga por TB escaneado. Un Trino o un
Redshift propio darían el mismo SQL a cambio de un servicio prendido; Athena es la única
opción que respeta el costo cero en reposo. El workgroup fuerza la ubicación de resultados,
que una regla de ciclo de vida del bucket limpia a los siete días.

**Step Functions y no MWAA.** Un entorno de MWAA cuesta del orden de 350 USD al mes esté o no
corriendo algo: es la opción más cara de todo el proyecto. Step Functions cobra por transición
de estado (los primeros 4.000 pasos mensuales son gratis) y lo que hay que orquestar son pocas
tareas en serie: ingesta, bronze y un silver por contrato en cada pipeline de fuente (producción
tiene dos, las DDJJ y el padrón de pozos), y una sola tarea en el de gold. La máquina de estados
usa `glue:startJobRun.sync`, que espera a que cada job termine y falla la ejecución si el job
falla: la misma semántica que una dependencia en cualquier orquestador.

**Neon y no RDS.** El manifiesto de ingesta necesita Postgres. La instancia más chica de RDS
cuesta unos 12 USD al mes corriendo todo el día, y apagarla entre corridas la deja igual
pagando el almacenamiento. Neon da un Postgres serverless gratis con escalado a cero, que es
exactamente el patrón de uso: unas pocas consultas por corrida mensual. La cadena de conexión
vive en SSM Parameter Store como SecureString y los jobs reciben el *nombre* del parámetro,
nunca el valor: un secreto en los argumentos de un job queda visible en la consola y en
`get-job-runs`.

**State de Terraform: local mientras el operador sea uno.** El entorno es efímero: se crea, se
demuestra y se destruye. Un backend remoto pide un bucket y una tabla de locks que sobreviven
al `destroy`, y con una sola persona aplicando desde una máquina no hay con quién coordinar.
`*.tfstate` está en el `.gitignore`.

Esa condición se cayó el 2026-09-12, cuando el despliegue pasó a GitHub Actions: un runner
arranca vacío y con state local creería que no existe nada. El state vive ahora en S3 con
bloqueo en DynamoDB (`infra/terraform/bootstrap/`, ADR 0005), y cuesta unos KB en S3 más una
tabla en `PAY_PER_REQUEST`.

## Consecuencias

- El costo del entorno en reposo es cero: S3 con unos pocos MB y nada más. Solo se paga cuando
  alguien dispara la máquina de estados.
- Los schedules de EventBridge nacen deshabilitados (`enable_schedule = false`). Se habilitan a
  propósito cuando se quiere dejar el pipeline corriendo solo.
- Gold también entra en este esquema, pero no como job de Spark: corre dbt contra Athena dentro
  de un job de Glue (ADR 0003).
- Los jobs de Glue admiten una corrida a la vez y los pipelines de fuente comparten
  `ingest_landing` y `silver_load`, así que no van en paralelo. Cada paso reintenta
  `Glue.ConcurrentRunsExceededException` cada minuto hasta 30 veces: el que llega segundo
  espera a que se libere el job —hasta media hora— en vez de fallar al instante. Medido en
  dev el 2026-09-12: el segundo silver de producción cayó en ese error porque Glue todavía
  contaba el clúster del primero como activo; con 60 s de espera se resuelve en un intento.
- El state de los ambientes está en S3 y versionado. El que sigue siendo local es el de
  `bootstrap/`: son diez recursos que casi nunca cambian y, si se pierde el archivo, se
  reimportan.
- Perder la cuenta de Neon deja el manifiesto sin backend: los datos de landing siguen en S3
  pero bronze no sabe qué cargar. La reconstrucción es correr la ingesta de nuevo.
