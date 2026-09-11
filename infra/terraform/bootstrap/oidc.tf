# Cómo se autentica GitHub Actions contra AWS: OIDC y ningún secreto.
#
# El runner le pide a GitHub un token firmado que dice de qué repo, de qué rama y de qué
# workflow viene, y AWS lo cambia por credenciales temporales si el token coincide con la
# trust policy de abajo. No hay `AWS_ACCESS_KEY_ID` en los secretos del repo, así que no hay
# nada que rotar ni nada que se filtre en un fork.

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # El único `aud` que emite `aws-actions/configure-aws-credentials`.
  client_id_list = ["sts.amazonaws.com"]

  # AWS dejó de validar esta huella en 2023 (usa las CA públicas), pero el campo sigue siendo
  # obligatorio y este es el valor que documenta GitHub.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

locals {
  # `sub` es el claim que dice quién pide el token. Cada ambiente confía en uno distinto:
  #
  #   dev  -> el GitHub Environment `dev`, que es el que declara el job `deploy-dev`; los
  #           push a `main`, y además los `pull_request`, porque el workflow corre
  #           `terraform plan` de dev en cada PR y un plan necesita leer la cuenta.
  #   prod -> solo el GitHub Environment `prod`. Ese claim aparece únicamente cuando el job
  #           declara `environment: prod`, y ese environment está configurado con aprobación
  #           manual y con "deployment branches: main only". Es más ajustado que mirar la
  #           rama: ata el rol a la puerta que hay que abrir a mano.
  sujetos = {
    dev = [
      # Con un `environment:` declarado, GitHub emite este claim y no el de la rama: sin esta
      # línea el job `deploy-dev` de deploy.yml no puede asumir el rol.
      "repo:${var.github_repository}:environment:dev",
      "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}",
      "repo:${var.github_repository}:pull_request",
    ]
    prod = [
      "repo:${var.github_repository}:environment:prod",
    ]
  }
}

data "aws_iam_policy_document" "confiar_en_github" {
  for_each = local.sujetos

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringLike y no StringEquals porque `sujetos` puede traer más de un valor y porque el
    # claim de pull_request no lleva número de PR: son cadenas exactas igual, sin comodines.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

resource "aws_iam_role" "github" {
  for_each = local.sujetos

  name               = "${var.project}-github-${each.key}"
  description        = "Rol que asume GitHub Actions para desplegar el ambiente ${each.key}."
  assume_role_policy = data.aws_iam_policy_document.confiar_en_github[each.key].json

  # Una hora: un `apply` completo del lakehouse tarda minutos, no horas.
  max_session_duration = 3600
}

# --- qué puede hacer cada rol ------------------------------------------------

locals {
  cuenta = data.aws_caller_identity.actual.account_id
  glue   = "arn:aws:glue:${var.region}:${local.cuenta}"
}

data "aws_iam_policy_document" "desplegar" {
  for_each = local.sujetos

  # El bucket del lakehouse de su ambiente y nada más: el rol de dev no puede tocar un objeto
  # del bucket de prod ni por error de tipeo en un tfvars.
  statement {
    sid     = "ElBucketDeSuAmbiente"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::oil-lakehouse-${local.cuenta}-${each.key}",
      "arn:aws:s3:::oil-lakehouse-${local.cuenta}-${each.key}/*",
    ]
  }

  # El state remoto: leer y escribir su propio archivo, no el del otro ambiente.
  statement {
    sid       = "SuStateRemoto"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/env:/${each.key}/*"]
  }

  statement {
    sid       = "ListarElBucketDelState"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "TomarElLock"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.locks.arn]
  }

  # Glue: las tres bases del ambiente y los jobs con su sufijo. El `catalog` es obligatorio
  # en toda llamada al Data Catalog, aunque la acción sea sobre una base.
  statement {
    sid     = "ElCatalogoYLosJobsDeSuAmbiente"
    actions = ["glue:*"]
    resources = [
      "${local.glue}:catalog",
      "${local.glue}:database/bronze_${each.key}",
      "${local.glue}:database/silver_${each.key}",
      "${local.glue}:database/gold_${each.key}",
      "${local.glue}:table/bronze_${each.key}/*",
      "${local.glue}:table/silver_${each.key}/*",
      "${local.glue}:table/gold_${each.key}/*",
      "${local.glue}:job/*_${each.key}",
    ]
  }

  statement {
    sid     = "LasMaquinasDeEstadosYLosSchedules"
    actions = ["states:*", "scheduler:*"]
    resources = [
      "arn:aws:states:${var.region}:${local.cuenta}:stateMachine:*_${each.key}",
      "arn:aws:scheduler:${var.region}:${local.cuenta}:schedule/default/*-${each.key}",
    ]
  }

  statement {
    sid       = "ElWorkgroupDeAthena"
    actions   = ["athena:*"]
    resources = ["arn:aws:athena:${var.region}:${local.cuenta}:workgroup/oil-lakehouse-${each.key}"]
  }

  # Los tres roles que crea el ambiente. `iam:PassRole` está acotado igual: sin eso, quien
  # tuviera este rol podría hacerle ejecutar a Glue cualquier otro rol de la cuenta.
  statement {
    sid = "SusPropiosRolesDeEjecucion"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:GetRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${local.cuenta}:role/${var.project}-*-${each.key}"]
  }

  # Terraform lee estas dos en cada plan para resolver `data.aws_caller_identity` y las
  # políticas administradas que se adjuntan a los roles. No admiten acotar por recurso.
  statement {
    sid       = "LecturasQueNoAdmitenRecurso"
    actions   = ["sts:GetCallerIdentity", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "desplegar" {
  for_each = local.sujetos

  name   = "desplegar-${each.key}"
  role   = aws_iam_role.github[each.key].id
  policy = data.aws_iam_policy_document.desplegar[each.key].json
}
