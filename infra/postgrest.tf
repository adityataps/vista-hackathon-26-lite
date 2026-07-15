# ── PostgREST — auto-REST API over the PayInvestigator schema ────────────────

resource "aws_security_group" "postgrest_task" {
  name   = "${var.app_name}-postgrest-task"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "rds_ingress_postgrest" {
  type                     = "ingress"
  security_group_id        = aws_security_group.rds.id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.postgrest_task.id
}

resource "aws_lb_target_group" "postgrest" {
  name        = "${var.app_name}-postgrest"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path                = "/"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    matcher             = "200"
  }
}

# Priority 5 — above /api/* (10) and the frontend default
resource "aws_lb_listener_rule" "postgrest" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 5

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.postgrest.arn
  }

  condition {
    path_pattern {
      values = ["/rest/*"]
    }
  }
}

resource "aws_cloudwatch_log_group" "postgrest" {
  name              = "/ecs/${var.app_name}-postgrest"
  retention_in_days = 7
}

resource "aws_ecs_task_definition" "postgrest" {
  family                   = "${var.app_name}-postgrest"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn

  container_definitions = jsonencode([{
    name  = "postgrest"
    image = "postgrest/postgrest:v12.0.2"
    portMappings = [{
      containerPort = 3000
      protocol      = "tcp"
    }]
    environment = [
      { name = "PGRST_DB_SCHEMA",                  value = "public" },
      { name = "PGRST_DB_ANON_ROLE",               value = "web_anon" },
      { name = "PGRST_SERVER_PORT",                 value = "3000" },
      { name = "PGRST_DB_POOL",                     value = "5" },
      { name = "PGRST_SERVER_CORS_ALLOWED_ORIGINS", value = "*" },
    ]
    secrets = [
      { name = "PGRST_DB_URI", valueFrom = aws_ssm_parameter.db_url.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.postgrest.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "postgrest" {
  name            = "${var.app_name}-postgrest"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.postgrest.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.public.ids
    security_groups  = [aws_security_group.postgrest_task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.postgrest.arn
    container_name   = "postgrest"
    container_port   = 3000
  }

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [
    aws_lb_listener.https,
    null_resource.postgrest_db_setup,
  ]
}

# Bootstrap the DB: create web_anon role + exception_queue view.
# Runs locally via psql; re-runs only if the DB URL changes.
resource "null_resource" "postgrest_db_setup" {
  triggers = {
    db_url = aws_ssm_parameter.db_url.value
  }

  provisioner "local-exec" {
    command = <<-BASH
      psql "${aws_ssm_parameter.db_url.value}" <<'PSQL'
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'web_anon') THEN
            CREATE ROLE web_anon NOLOGIN;
          END IF;
        END
        $$;

        GRANT USAGE ON SCHEMA public TO web_anon;
        GRANT SELECT ON exceptions, payments, investigations, payment_events TO web_anon;

        CREATE OR REPLACE VIEW exception_queue AS
        SELECT
          e.id,
          CASE WHEN p.id IS NOT NULL
               THEN 'TX-' || LPAD(p.id::text, 5, '0')
               ELSE e.msg_id
          END                   AS tx_id,
          e.msg_id,
          e.uetr,
          e.detected_errors,
          e.status,
          e.created_at,
          p.amount,
          p.currency,
          p.debtor_name         AS sender,
          p.creditor_name       AS receiver,
          p.sender_bic,
          p.receiver_bic,
          p.debtor_iban,
          p.creditor_iban
        FROM exceptions e
        LEFT JOIN payments p ON p.msg_id = e.msg_id
        ORDER BY e.created_at DESC;

        GRANT SELECT ON exception_queue TO web_anon;
      PSQL
    BASH
  }
}
