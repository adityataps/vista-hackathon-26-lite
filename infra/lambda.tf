resource "aws_lambda_function" "backend" {
  function_name = "${var.app_name}-backend"
  role          = aws_iam_role.lambda_backend.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.backend.repository_url}:latest"
  timeout       = 180
  memory_size   = 2048
  architectures = ["x86_64"]

  environment {
    variables = {
      AWS_LWA_INVOKE_MODE    = "response_stream"
      PORT                   = "8080"
      S3_BUCKET              = aws_s3_bucket.mockdata.bucket
      GUARDRAIL_ID           = aws_bedrock_guardrail.pay_investigator.guardrail_id
      GUARDRAIL_VERSION      = aws_bedrock_guardrail_version.pay_investigator.version
      BEDROCK_MODEL_ID       = local.haiku_model_id
      BEDROCK_EMBED_MODEL_ID = local.titan_embed_model
      BEDROCK_DAILY_LIMIT    = tostring(var.bedrock_daily_limit)
      DB_CLUSTER_ARN         = aws_rds_cluster.main.arn
      DB_SECRET_ARN          = aws_secretsmanager_secret.db_credentials.arn
      DB_NAME                = local.db_name
      LANGCHAIN_TRACING_V2   = var.langsmith_api_key != "" ? "true" : "false"
      LANGCHAIN_PROJECT      = var.langsmith_project
      LANGCHAIN_API_KEY      = var.langsmith_api_key
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.backend,
    aws_iam_role_policy_attachment.lambda_backend_basic,
    aws_secretsmanager_secret_version.db_credentials,
  ]

  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_function_url" "backend" {
  function_name      = aws_lambda_function.backend.function_name
  authorization_type = "NONE"
  invoke_mode        = "RESPONSE_STREAM"
}

# Required alongside authorization_type = "NONE" above: Function URLs need an
# explicit resource-based policy statement to actually permit unauthenticated
# invocation, otherwise every request 403s regardless of authorization_type.
resource "aws_lambda_permission" "backend_function_url_public" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.backend.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# As of Oct 2025, AWS requires a SECOND statement granting lambda:InvokeFunction
# (scoped via the lambda:InvokedViaFunctionUrl condition) in addition to
# lambda:InvokeFunctionUrl above. Without this, every Function URL request 403s
# with AccessDeniedException even though authorization_type is NONE and the
# InvokeFunctionUrl permission is present.
# See https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
#
# NOTE: the `invoked_via_function_url` argument on aws_lambda_permission was
# only added to the AWS provider in v6.55.0; this repo pins provider v5.x, so
# we shell out via the CLI instead. Once the provider is upgraded to >= 6.55.0,
# replace this with a native aws_lambda_permission resource (see git history
# of this file for the exact resource block).
resource "null_resource" "backend_function_url_invoke_permission" {
  triggers = {
    function_name = aws_lambda_function.backend.function_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      if [ -n "${var.aws_profile}" ] && [ "${var.aws_profile}" != "default" ]; then
        export AWS_PROFILE="${var.aws_profile}"
      fi
      aws lambda add-permission \
        --function-name ${aws_lambda_function.backend.function_name} \
        --statement-id AllowPublicFunctionInvokeViaUrl \
        --action lambda:InvokeFunction \
        --principal '*' \
        --invoked-via-function-url \
        --region ${var.region} \
        || true
    EOT
  }

  depends_on = [aws_lambda_permission.backend_function_url_public]
}

resource "aws_lambda_function" "payment_ingest" {
  function_name = "${var.app_name}-payment-xml-ingest"
  role          = aws_iam_role.lambda_ingest.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.ingest.repository_url}:latest"
  timeout       = 60
  memory_size   = 512
  architectures = ["x86_64"]

  image_config {
    command = ["handler.lambda_handler"]
  }

  environment {
    variables = {
      DB_CLUSTER_ARN            = aws_rds_cluster.main.arn
      DB_SECRET_ARN             = aws_secretsmanager_secret.db_credentials.arn
      DB_NAME                   = local.db_name
      REFERENCE_DATA_S3_URI     = "s3://${aws_s3_bucket.mockdata.id}/${local.reference_data_prefix}"
      ERROR_NOTIFY_ENDPOINT_URL = var.error_notify_endpoint_url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_ingest,
    aws_iam_role_policy_attachment.lambda_ingest_basic,
    aws_secretsmanager_secret_version.db_credentials,
  ]

  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_event_source_mapping" "payment_ingest" {
  event_source_arn = aws_sqs_queue.payment_ingest.arn
  function_name    = aws_lambda_function.payment_ingest.arn
  batch_size       = 10
  enabled          = true
}
