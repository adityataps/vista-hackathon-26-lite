variable "region" {
  default = "us-east-1"
}

variable "aws_profile" {
  default = "default"
}

variable "app_name" {
  default = "payinvestigator"
}

variable "bedrock_daily_limit" {
  description = "Soft per-day cap for all Bedrock invocations made by the backend Lambda (chat + embeddings)."
  type        = number
  default     = 100
}

variable "budget_alert_email" {
  description = "Email address subscribed to the Bedrock budget alert SNS topic. Required before first apply."
  type        = string
}

variable "error_notify_endpoint_url" {
  description = "POST target the payment-ingest Lambda calls with {payment_id, error_msg} when a payment error is detected. Leave blank to disable notifications."
  type        = string
  default     = ""
}

variable "langsmith_api_key" {
  description = "LangSmith API key used for LangGraph/LangChain tracing. Leave blank to disable tracing."
  type        = string
  sensitive   = true
  default     = ""
}

variable "langsmith_project" {
  description = "LangSmith project name traces are grouped under."
  type        = string
  default     = "payinvestigator"
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token (DNS edit scope) for managing the vistahack26.tapshalkar.com record. Pass via TF_VAR_cloudflare_api_token env var, never commit to tfvars."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for tapshalkar.com."
  type        = string
  default     = "9a2b68936aec95fc2ad33a144cec981a"
}

variable "custom_domain" {
  description = "Custom subdomain the frontend is served from, proxied through Cloudflare."
  type        = string
  default     = "vistahack26.tapshalkar.com"
}
