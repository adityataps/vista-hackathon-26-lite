resource "aws_bedrock_guardrail" "pay_investigator" {
  name                      = "${var.app_name}-guardrail"
  description               = "PayInvestigator guardrail — topic restriction, PII redaction, content filtering"
  blocked_input_messaging   = "This request is outside the scope of payment investigation."
  blocked_outputs_messaging = "Response blocked by compliance policy."

  # Restrict to payment-investigation topics only
  topic_policy_config {
    topics_config {
      name       = "non-payment-topics"
      type       = "DENY"
      definition = "Any topic unrelated to payment processing, SWIFT/pacs.008 messages, IBAN/BIC validation, sanctions screening, or financial compliance."
      examples   = ["Tell me a joke", "Write me code", "What is the weather"]
    }
  }

  # Redact PII in traces/logs — surfaces to agent but masked in audit output
  sensitive_information_policy_config {
    pii_entities_config {
      type   = "INTERNATIONAL_BANK_ACCOUNT_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "SWIFT_CODE"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
  }

  # Block harmful content
  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
  }
}

resource "aws_bedrock_guardrail_version" "pay_investigator" {
  guardrail_arn = aws_bedrock_guardrail.pay_investigator.guardrail_arn
  description   = "v1"
}

resource "aws_ssm_parameter" "guardrail_id" {
  name  = "/${var.app_name}/guardrail_id"
  type  = "String"
  value = aws_bedrock_guardrail.pay_investigator.guardrail_id
}
