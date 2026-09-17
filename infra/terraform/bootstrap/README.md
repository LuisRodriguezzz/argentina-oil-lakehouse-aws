# Bootstrap: lo que tiene que existir antes de los ambientes

Diez recursos de Terraform que no pertenecen a `dev` ni a `prod` sino a los dos, y que por eso
no pueden vivir en `../`: un `terraform destroy` de un ambiente se los llevaría puestos. Son el
bucket de state con su versionado, cifrado y bloqueo de acceso público, la tabla de locks, el
proveedor OIDC y los dos roles con sus políticas.

| Recurso | Para qué |
| --- | --- |
| Bucket `oil-lakehouse-tfstate-<cuenta>` (versionado, cifrado, sin acceso público) | State remoto de `../`, un archivo por workspace (`env:/dev/...`, `env:/prod/...`). |
| Tabla DynamoDB `oil-lakehouse-tfstate-locks` | Bloqueo, para que la máquina del autor y el workflow de GitHub no apliquen a la vez. |
| Proveedor OIDC de `token.actions.githubusercontent.com` | Que AWS acepte los tokens que emite GitHub Actions. |
| Roles `argentina-oil-lakehouse-github-dev` y `-prod` | Lo que asume el workflow. Sin claves de acceso en los secretos del repo. |

El state de este directorio es local, en `bootstrap/terraform.tfstate`: es el único que no
puede guardarse en el bucket que él mismo crea. El de `../` vive en ese bucket, un archivo por
workspace.

En reposo cuesta prácticamente cero: S3 con unos KB de state y una tabla de DynamoDB en
`PAY_PER_REQUEST` que solo se escribe durante un `apply`.

## Cómo se aplicó

```powershell
cd infra\terraform\bootstrap
terraform init
terraform plan            # mirar los diez recursos antes de crearlos
terraform apply
```

Después, en este orden:

1. Escribir el bloque `backend "s3"` en `../versions.tf` con el bucket que devolvió el output
   `state_bucket` y la tabla de `lock_table`.
2. Por cada ambiente, migrar el state local al remoto:
   `terraform workspace select dev; terraform init -migrate-state`. Terraform pregunta si
   copia el state que ya existe: sí.
3. Cargar `github_role_arns` en las variables de repo `AWS_ROLE_DEV` y `AWS_ROLE_PROD`.
4. Crear los dos GitHub Environments. `prod` con "Required reviewers" (uno alcanza) y
   "Deployment branches: main only"; `dev` sin reviewers ni restricciones. Los dos hacen falta
   porque los jobs `deploy-dev` y `deploy-prod` declaran `environment:`, y en cuanto un job lo
   declara GitHub emite el claim `environment:<nombre>` en vez del de la rama.
5. Poner la variable de repo `DEPLOY_ENABLED = true`, que es lo que destraba los jobs del
   workflow.

Es un procedimiento de una sola vez. Volver a correrlo solo tiene sentido en una cuenta de AWS
nueva, o si se pierde el state local de este directorio (ahí se reimporta con
`terraform import`).

## Trust policy: quién puede asumir cada rol

El claim `sub` que emite GitHub lleva los ids numéricos del dueño y del repo, no solo sus
nombres: `repo:<owner>@<id>/<repo>@<id>:...` (ver `variables.tf`).

- **dev** confía en `…:environment:dev`, en `…:ref:refs/heads/main` y en `…:pull_request`. El
  primero es el que usa el job `deploy-dev`, que declara `environment: dev`; el de la rama
  queda para un job de push sin environment, y el de `pull_request` hace falta porque el
  `terraform plan` de cada PR necesita leer la cuenta. Un fork no puede: los tokens de un PR
  desde un fork no llevan el `sub` del repo original.
- **prod** confía solo en `…:environment:prod`. Ese claim aparece únicamente cuando el job
  declara `environment: prod`, y ese environment tiene aprobación manual y está restringido a
  `main`. Es más ajustado que mirar la rama: ata el rol a la puerta que hay que abrir a mano.

Los permisos de cada rol están acotados por sufijo de ambiente (`oil-lakehouse-<cuenta>-dev`,
`job/*_dev`, `stateMachine:*_dev`, `role/argentina-oil-lakehouse-*-dev`) y a su propio prefijo
del state remoto. Si un `apply` llegara a fallar con `AccessDenied`, el mensaje nombra la
acción y el ARN exactos: se agrega esa acción a `oidc.tf` en vez de aflojar el recurso a `*`.
