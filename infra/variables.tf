variable "region" {
  default = "us-east-1"
}

variable "aws_profile" {
  default = "default"
}

variable "app_name" {
  default = "payinvestigator"
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
