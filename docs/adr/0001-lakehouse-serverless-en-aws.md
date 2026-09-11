# ADR 0001 — Lakehouse serverless: Glue, Step Functions y Athena

**Estado:** aceptada · 2026-09-11

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

**State de Terraform local.** El entorno es efímero: se crea, se demuestra y se destruye. Un
backend remoto pediría un bucket y una tabla de locks que sobrevivirían al `destroy` y
costarían plata para nada, y no hay un segundo operador con quien coordinar. `*.tfstate` está
en el `.gitignore`. El backend S3 queda escrito y comentado en `infra/terraform/versions.tf`
para el día que haga falta (ADR 0005).

## Consecuencias

- El costo del entorno en reposo es cero: S3 con unos pocos MB y nada más. Solo se paga cuando
  alguien dispara la máquina de estados.
- Los schedules de EventBridge nacen deshabilitados (`enable_schedule = false`). Se habilitan a
  propósito cuando se quiere dejar el pipeline corriendo solo.
- Gold también entra en este esquema, pero no como job de Spark: corre dbt contra Athena dentro
  de un job de Glue (ADR 0003).
- Los jobs de Glue admiten una corrida a la vez y los pipelines de fuente comparten
  `ingest_landing` y `silver_load`, así que no van en paralelo. Cada paso reintenta
  `Glue.ConcurrentRunsExceededException` cada 5 minutos hasta 10 veces: el que llega segundo
  espera a que se libere el job —casi una hora— en vez de fallar al instante.
- Si se pierde el `terraform.tfstate` hay que reimportar o destruir a mano. Es el precio
  aceptado por no sostener infraestructura para el propio Terraform.
- Perder la cuenta de Neon deja el manifiesto sin backend: los datos de landing siguen en S3
  pero bronze no sabe qué cargar. La reconstrucción es correr la ingesta de nuevo.
