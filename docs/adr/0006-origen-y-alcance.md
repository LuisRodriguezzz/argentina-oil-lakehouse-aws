# ADR 0006 — Origen del repo y alcance: solo AWS

**Estado:** aceptada · 2026-09-11

## Contexto

Este repositorio deriva de [`ypf-data-platform`](https://github.com/LuisRodriguezzz/ypf-data-platform),
que estaba organizado como "un stack, dos destinos": el mismo código de transformación corría
sobre una máquina local (MinIO, un Spark en contenedor, un catálogo Iceberg propio, Airflow,
Kafka y MLflow) y sobre AWS (S3, Glue, Step Functions, Athena).

Sostener los dos destinos tenía un costo visible en el código. La SparkSession se armaba de dos
formas, dbt tenía dos targets con dos adaptadores, cada nombre de tabla dependía del destino y
había ramas por destino en la configuración y en la orquestación. Nada de eso es interesante de
leer, y buena parte del esfuerzo se iba en mantener la paridad entre dos entornos en vez de en
el pipeline.

## Decisión

**Un solo destino: AWS.** Se quitó el destino local completo y, con él, las piezas que solo
existían ahí: streaming con Kafka, el modelo de ML con MLflow y los modelos de monitoreo. La
orquestación con Airflow se reemplaza por Step Functions, que ya era el equivalente en la nube
(ADR 0001).

Lo que queda es el camino completo de datos: ingesta a landing, bronze y silver sobre Iceberg
con contratos y cuarentena, gold con dbt sobre Athena, orquestado por Step Functions y
desplegado con Terraform en dos ambientes.

**Por qué.** Con un solo destino el código es más corto y se lee de una sola manera: una forma
de armar la SparkSession, un solo target de dbt, ningún condicional por destino, ninguna
configuración que elegir. Y deja espacio para lo que el proyecto de origen no llegaba a
mostrar: el despliegue cloud tratado como producto —ambientes separados, state remoto,
CI/CD con OIDC y aprobación manual— en vez de un `terraform apply` a mano.

**Qué queda fuera y por qué.**

- **Streaming.** Rompe el principio de costo cero en reposo (ADR 0001). Kinesis se cobra por
  hora de shard esté o no pasando tráfico, y un clúster de MSK factura mientras existe: un
  stream necesita algo escuchando todo el tiempo, que es exactamente lo contrario de este
  diseño.
- **ML.** No está descartado: queda para una segunda etapa, como un job de Glue más colgado de
  la máquina de estados que lea gold y escriba una tabla de predicciones. No entró ahora porque
  MLflow no tiene en AWS una opción gratuita —el tracking server administrado se paga por hora
  esté o no en uso— y montarlo aparte sería el único servicio prendido de todo el proyecto.
- **Monitoreo.** Los modelos de monitoreo de dbt del proyecto de origen miraban tablas que solo
  existían en el destino local y leían metadata de Iceberg con sintaxis de Spark. Rehacerlos
  sobre Athena es trabajo pendiente, no algo que se pueda copiar.
- **Airflow.** Step Functions cumple el mismo rol —dependencias entre tareas, reintentos, una
  ejecución por corrida— sin un servicio prendido: un entorno de MWAA es el recurso más caro que
  este proyecto podría tener (ADR 0001).

**Qué agrega respecto del original.** El despliegue cloud completo, y los tres pasos ya
corrieron contra AWS: dev y prod aplicados (2026-09-11 y 2026-09-12), el state de Terraform en
S3 con la tabla de locks de `infra/terraform/bootstrap/`, y `deploy.yml` habilitado con los
roles de OIDC (ADR 0005).

## Consecuencias

- Los números que cita el README se midieron en `prod` de este repo el 2026-09-12 y coinciden
  con los del proyecto de origen del 2026-09-06 en todo lo que no depende de republicaciones
  del portal. El README lo dice en "Resultados".
- El repositorio de origen queda como está y no se toca: es de solo lectura. Quien quiera ver el
  destino local, el streaming o el modelo de ML, los encuentra ahí.
- El nombre del proyecto cambió: el dominio son los datos públicos del upstream argentino, no
  una compañía. YPF aparece en los datos como la operadora que más declara, y nada más.
