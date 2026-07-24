resource "aws_lambda_function" "backend" {
  function_name = "${var.app_name}-backend"
  role          = aws_iam_role.lambda_backend.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.backend.repository_url}:latest"
  timeout       = 180
  memory_size   = 2048
  architectures = ["x86_64"]

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.lambda_backend.id]
  }

  environment {
    variables = {
      AWS_DEFAULT_REGION     = var.region
      AWS_LWA_INVOKE_MODE    = "response_stream"
      PORT                   = "8080"
      S3_BUCKET              = aws_s3_bucket.mockdata.bucket
      GUARDRAIL_ID           = aws_bedrock_guardrail.pay_investigator.guardrail_id
      GUARDRAIL_VERSION      = aws_bedrock_guardrail_version.pay_investigator.version
      BEDROCK_MODEL_ID       = local.haiku_model_id
      BEDROCK_EMBED_MODEL_ID = local.titan_embed_model
      DATABASE_URL           = aws_ssm_parameter.db_url.value
      LANGCHAIN_TRACING_V2   = var.langsmith_api_key != "" ? "true" : "false"
      LANGCHAIN_PROJECT      = var.langsmith_project
      LANGCHAIN_API_KEY      = var.langsmith_api_key
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.backend,
    aws_iam_role_policy_attachment.lambda_backend_basic,
    aws_iam_role_policy_attachment.lambda_backend_vpc,
    aws_vpc_endpoint.bedrock_runtime,
    aws_vpc_endpoint.s3,
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

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.lambda_ingest.id]
  }

  environment {
    variables = {
      DATABASE_URL              = aws_ssm_parameter.db_url.value
      REFERENCE_DATA_S3_URI     = "s3://${aws_s3_bucket.mockdata.id}/${local.reference_data_prefix}"
      ERROR_NOTIFY_ENDPOINT_URL = var.error_notify_endpoint_url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_ingest,
    aws_iam_role_policy_attachment.lambda_ingest_basic,
    aws_iam_role_policy_attachment.lambda_ingest_vpc,
    aws_vpc_endpoint.s3,
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
