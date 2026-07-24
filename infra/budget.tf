resource "aws_sns_topic" "bedrock_budget_alerts" {
  name = "${var.app_name}-bedrock-budget-alerts"
}

data "aws_iam_policy_document" "bedrock_budget_alerts" {
  statement {
    sid    = "AllowBudgetsPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.bedrock_budget_alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:budgets::${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_sns_topic_policy" "bedrock_budget_alerts" {
  arn    = aws_sns_topic.bedrock_budget_alerts.arn
  policy = data.aws_iam_policy_document.bedrock_budget_alerts.json
}

resource "aws_sns_topic_subscription" "bedrock_budget_email" {
  topic_arn = aws_sns_topic.bedrock_budget_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_alert_email
}

resource "aws_budgets_budget" "bedrock_monthly" {
  name         = "${var.app_name}-bedrock-monthly"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "Service"
    values = ["Amazon Bedrock"]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.bedrock_budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.bedrock_budget_alerts.arn]
  }
}

data "aws_iam_policy_document" "budget_action_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "bedrock_budget_deny" {
  statement {
    effect = "Deny"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "bedrock_budget_deny" {
  name        = "${var.app_name}-bedrock-budget-deny"
  description = "AWS Budgets backstop deny policy for Bedrock runtime access"
  policy      = data.aws_iam_policy_document.bedrock_budget_deny.json
}

resource "aws_iam_role" "budget_action" {
  name               = "${var.app_name}-budget-action"
  assume_role_policy = data.aws_iam_policy_document.budget_action_assume.json
}

data "aws_iam_policy_document" "budget_action" {
  statement {
    effect = "Allow"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [aws_iam_role.lambda_backend.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PolicyARN"
      values   = [aws_iam_policy.bedrock_budget_deny.arn]
    }
  }

  statement {
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:ListAttachedRolePolicies",
    ]
    resources = [aws_iam_role.lambda_backend.arn]
  }
}

resource "aws_iam_role_policy" "budget_action" {
  name   = "${var.app_name}-budget-action"
  role   = aws_iam_role.budget_action.id
  policy = data.aws_iam_policy_document.budget_action.json
}

resource "aws_budgets_budget_action" "bedrock_hard_cutoff" {
  budget_name        = aws_budgets_budget.bedrock_monthly.name
  action_type        = "APPLY_IAM_POLICY"
  approval_model     = "AUTOMATIC"
  notification_type  = "ACTUAL"
  execution_role_arn = aws_iam_role.budget_action.arn

  action_threshold {
    action_threshold_type  = "PERCENTAGE"
    action_threshold_value = 100
  }

  definition {
    iam_action_definition {
      policy_arn = aws_iam_policy.bedrock_budget_deny.arn
      roles      = [aws_iam_role.lambda_backend.name]
    }
  }

  # This is a backstop, not an instant kill switch: Budgets runs on billing
  # data with some lag. AWS resets the action at the next monthly budget period.
  subscriber {
    address           = var.budget_alert_email
    subscription_type = "EMAIL"
  }
}
