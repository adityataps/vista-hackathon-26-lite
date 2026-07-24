resource "aws_cloudwatch_log_group" "backend" {
  name              = "/aws/lambda/${var.app_name}-backend"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "lambda_ingest" {
  name              = "/aws/lambda/${var.app_name}-payment-xml-ingest"
  retention_in_days = 7
}
