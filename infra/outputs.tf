output "backend_function_url" {
  value = aws_lambda_function_url.backend.function_url
}

output "frontend_website_url" {
  description = "Raw S3 static-website URL (HTTP only, direct origin, bypasses Cloudflare proxy)."
  value       = "http://${aws_s3_bucket.frontend.bucket}.s3-website-${var.region}.amazonaws.com"
}

output "frontend_custom_domain_url" {
  description = "Custom domain URL, served over HTTPS via Cloudflare Universal SSL (Flexible mode)."
  value       = "https://${var.custom_domain}"
}

output "frontend_bucket_name" {
  value = aws_s3_bucket.frontend.bucket
}

output "mockdata_bucket_name" {
  value = aws_s3_bucket.mockdata.bucket
}

output "knowledge_base_bucket_name" {
  value = aws_s3_bucket.knowledge_base.bucket
}

output "ecr_backend_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "ecr_ingest_url" {
  value = aws_ecr_repository.ingest.repository_url
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "aurora_cluster_endpoint" {
  value = aws_rds_cluster.main.endpoint
}

output "aurora_reader_endpoint" {
  value = aws_rds_cluster.main.reader_endpoint
}

output "sqs_payment_ingest_url" {
  value = aws_sqs_queue.payment_ingest.url
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.pay_investigator.guardrail_id
}

output "guardrail_arn" {
  value = aws_bedrock_guardrail.pay_investigator.guardrail_arn
}
