data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_backend" {
  name               = "${var.app_name}-lambda-backend"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_backend_basic" {
  role       = aws_iam_role.lambda_backend.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role" "lambda_ingest" {
  name               = "${var.app_name}-lambda-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_ingest_basic" {
  role       = aws_iam_role.lambda_ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

locals {
  haiku_model_id      = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  haiku_foundation_id = "anthropic.claude-haiku-4-5-20251001-v1:0"
  titan_embed_model   = "amazon.titan-embed-text-v2:0"
}

data "aws_iam_policy_document" "lambda_backend" {
  statement {
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/${local.haiku_foundation_id}",
      "arn:aws:bedrock:${var.region}::foundation-model/${local.haiku_foundation_id}",
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${local.haiku_model_id}",
      "arn:aws:bedrock:*::foundation-model/${local.titan_embed_model}",
      "arn:aws:bedrock:${var.region}::foundation-model/${local.titan_embed_model}",
    ]
  }

  statement {
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.pay_investigator.guardrail_arn]
  }

  statement {
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.mockdata.arn,
      "${aws_s3_bucket.mockdata.arn}/*",
    ]
  }

  statement {
    actions = [
      "rds-data:ExecuteStatement",
      "rds-data:BatchExecuteStatement",
      "rds-data:BeginTransaction",
      "rds-data:CommitTransaction",
      "rds-data:RollbackTransaction",
    ]
    resources = [aws_rds_cluster.main.arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db_credentials.arn]
  }
}

resource "aws_iam_role_policy" "lambda_backend" {
  name   = "${var.app_name}-lambda-backend"
  role   = aws_iam_role.lambda_backend.id
  policy = data.aws_iam_policy_document.lambda_backend.json
}

locals {
  reference_data_prefix = "reference/"
}

data "aws_iam_policy_document" "lambda_ingest" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.mockdata.arn}/payments/*"]
  }

  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.mockdata.arn}/${local.reference_data_prefix}*"]
  }

  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.payment_ingest.arn]
  }

  statement {
    actions = [
      "rds-data:ExecuteStatement",
      "rds-data:BatchExecuteStatement",
      "rds-data:BeginTransaction",
      "rds-data:CommitTransaction",
      "rds-data:RollbackTransaction",
    ]
    resources = [aws_rds_cluster.main.arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db_credentials.arn]
  }
}

resource "aws_iam_role_policy" "lambda_ingest" {
  name   = "${var.app_name}-lambda-ingest"
  role   = aws_iam_role.lambda_ingest.id
  policy = data.aws_iam_policy_document.lambda_ingest.json
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # NOTE: this account uses GitHub's Enterprise Managed User (EMU) identity
    # format, so the OIDC "sub" claim includes each side's numeric GitHub ID
    # appended after "@" (e.g. "repo:adityataps@39311849/vista-hackathon-26-lite@1311420324:ref:...")
    # instead of the plain "repo:owner/repo:..." format used by regular
    # GitHub.com accounts. Confirmed via CloudTrail AssumeRoleWithWebIdentity
    # events after the plain-format condition below caused every OIDC
    # assumption to fail with "Not authorized to perform
    # sts:AssumeRoleWithWebIdentity". IDs are stable across repo renames, so
    # this is arguably more robust than name-based matching anyway.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:adityataps@39311849/vista-hackathon-26-lite@1311420324:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.app_name}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
}

data "aws_iam_policy_document" "github_actions" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
    ]
    resources = [
      aws_ecr_repository.backend.arn,
      aws_ecr_repository.ingest.arn,
    ]
  }

  statement {
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:GetFunctionUrlConfig",
    ]
    resources = [
      aws_lambda_function.backend.arn,
      aws_lambda_function.payment_ingest.arn,
    ]
  }

  statement {
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.frontend.arn]
  }

  statement {
    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObject",
    ]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "${var.app_name}-github-actions"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions.json
}
