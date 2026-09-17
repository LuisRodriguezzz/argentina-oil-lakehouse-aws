# ADR 0006 — Alcance: solo AWS, y qué queda afuera

**Estado:** aceptada · 2026-09-11 · reescrita el 2026-09-17

## Contexto

Un lakehouse de portfolio puede crecer en dos direcciones: hacia los lados, sumando piezas
(streaming, un modelo de ML, monitoreo, un orquestador con interfaz) o hacia adentro, tratando
lo que ya hace como un producto (ambientes separados, state remoto, despliegue automático con
aprobación, tests que corren en cada cambio). Las dos direcciones compiten por el mismo tiempo
y por el mismo presupuesto de unos pocos dólares.

También compiten por la legibilidad: cada destino, cada motor y cada servicio extra agrega una
rama más en la configuración, una forma más de armar la sesión de Spark y una decisión más que
quien lee el código tiene que reconstruir.

## Decisión

**Un solo destino, AWS, y el camino de datos completo antes que cualquier pieza lateral.** El
proyecto es la ingesta a landing, bronze y silver sobre Iceberg con contratos y cuarentena,
gold con dbt sobre Athena, orquestado por Step Functions y desplegado con Terraform en dos
ambientes, más el informe que se genera desde gold. Todo lo que no está en esa lista quedó
afuera a propósito.

**Por qué.** Con un solo destino el código se lee de una sola manera: una forma de armar la
SparkSession, un solo target de dbt, ningún condicional por entorno de ejecución, ninguna
configuración que elegir. Y el tiempo que no se gasta en piezas laterales va al despliegue
tratado como producto —ambientes separados, state remoto, CI/CD con OIDC y aprobación manual—
que es lo que distingue un pipeline que funciona en una máquina de uno que se puede operar.

**Qué queda afuera y por qué.**

- **Streaming.** Rompe el principio de costo cero en reposo (ADR 0001). Kinesis se cobra por
  hora de shard esté o no pasando tráfico, y un clúster de MSK factura mientras existe: un
  stream necesita algo escuchando todo el tiempo, que es exactamente lo contrario de este
  diseño.
- **ML.** No está descartado: queda para una segunda etapa, como un job de Glue más colgado de
  la máquina de estados que lea gold y escriba una tabla de predicciones. No entró ahora porque
  MLflow no tiene en AWS una opción gratuita —el tracking server administrado se paga por hora
  esté o no en uso— y montarlo aparte sería el único servicio prendido de todo el proyecto.
- **Monitoreo.** Una ejecución fallida queda en el historial de Step Functions y en los logs
  del job; nada avisa. Unos modelos de dbt que lean la metadata de Iceberg y el historial de
  calidad de silver (`dq_runs`) son trabajo pendiente.
- **Airflow.** Step Functions cumple el mismo rol —dependencias entre tareas, reintentos, una
  ejecución por corrida— sin un servicio prendido: un entorno de MWAA es el recurso más caro
  que este proyecto podría tener (ADR 0001).

## Consecuencias

- Todo el presupuesto, de tiempo y de dólares, va al camino de datos y a su despliegue. Nada
  queda prendido entre corridas.
- Lo que quedó afuera está listado en el README, en "Qué no hace", como límite y no como
  pendiente de redacción.
- El nombre del proyecto habla del dominio, los datos públicos del upstream argentino, y no de
  una compañía. YPF aparece en los datos como la operadora que más declara, y nada más.
