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
