# Bootstrap: lo que tiene que existir antes de los ambientes

Diez recursos que no pertenecen a `dev` ni a `prod` sino a los dos, y que por eso no pueden
vivir en `../`: un `terraform destroy` de un ambiente se los llevaría puestos.

| Recurso | Para qué |
| --- | --- |
| Bucket `oil-lakehouse-tfstate-<cuenta>` (versionado, cifrado, sin acceso público) | State remoto de `../`, un archivo por workspace (`env:/dev/...`, `env:/prod/...`). |
| Tabla DynamoDB `oil-lakehouse-tfstate-locks` | Bloqueo, para que la máquina del autor y el workflow de GitHub no apliquen a la vez. |
| Proveedor OIDC de `token.actions.githubusercontent.com` | Que AWS acepte los tokens que emite GitHub Actions. |
| Roles `argentina-oil-lakehouse-github-dev` y `-prod` | Lo que asume el workflow. Sin claves de acceso en los secretos del repo. |

## Nada de esto está aplicado

**Este directorio nunca se aplicó contra AWS.** Es la definición de lo que haría falta para
que `.github/workflows/deploy.yml` deje de estar deshabilitado; hoy el state de `../` sigue
siendo local y los despliegues se hacen a mano. Está en el repo porque la decisión ya está
tomada y escrita (ADR 0005), no porque esté funcionando.

Aplicarlo cuesta prácticamente cero en reposo: S3 con unos KB de state y una tabla de
DynamoDB en `PAY_PER_REQUEST` que solo se escribe durante un `apply`.

## Cuando se quiera aplicar

```powershell
cd infra\terraform\bootstrap
terraform init
terraform plan            # mirar los diez recursos antes de crearlos
terraform apply
```

Después, en este orden:

1. Descomentar el bloque `backend "s3"` de `../versions.tf` y completar el nombre del bucket
   con el `state_bucket` que devolvió el output.
2. Por cada ambiente, migrar el state local al remoto:
   `terraform workspace select dev; terraform init -migrate-state`. Terraform pregunta si
   copia el state que ya existe: sí.
3. Cargar `github_role_arns` en las variables `AWS_ROLE_DEV` y `AWS_ROLE_PROD` del repo.
4. Crear el GitHub Environment `prod` con "Required reviewers" (uno alcanza) y "Deployment
   branches: main only". La trust policy del rol de prod exige el claim
   `environment:prod`, así que sin ese environment el workflow no puede asumirlo.
5. Poner la variable de repo `DEPLOY_ENABLED = true`, que es lo que destraba los jobs del
   workflow.

## Trust policy: quién puede asumir cada rol

- **dev** confía en `repo:<owner>/<repo>:ref:refs/heads/main` y en
  `repo:<owner>/<repo>:pull_request`. El segundo hace falta porque el `terraform plan` de
  cada PR necesita leer la cuenta. Un fork no puede: los tokens de un PR desde un fork no
  llevan el `sub` del repo original.
- **prod** confía solo en `repo:<owner>/<repo>:environment:prod`. Ese claim aparece
  únicamente cuando el job declara `environment: prod`, y ese environment tiene aprobación
  manual y está restringido a `main`. Es más ajustado que mirar la rama: ata el rol a la
  puerta que hay que abrir a mano.

Los permisos de cada rol están acotados por sufijo de ambiente (`oil-lakehouse-<cuenta>-dev`,
`job/*_dev`, `stateMachine:*_dev`, `role/argentina-oil-lakehouse-*-dev`) y a su propio
prefijo del state remoto. Si un `apply` llegara a fallar con `AccessDenied`, el mensaje
nombra la acción y el ARN exactos: se agrega esa acción a `oidc.tf` en vez de aflojar el
recurso a `*`.
