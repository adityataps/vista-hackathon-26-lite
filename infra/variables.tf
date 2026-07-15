variable "region" {
  default = "us-west-2"
}

variable "aws_profile" {
  default = "default"
}

variable "app_name" {
  default = "payinvestigator"
}

variable "domain" {
  default = "vistahack26.tapshalkar.com"
}

variable "cloudflare_zone_id" {
  default = "9a2b68936aec95fc2ad33a144cec981a"
}

variable "cloudflare_api_token" {
  sensitive = true
}

variable "backend_url" {
  description = "HTTPS URL of the PayInvestigator backend (ALB)"
  type        = string
  default     = ""
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
