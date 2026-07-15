resource "random_password" "db" {
  length  = 24
  special = false
}

locals {
  db_name = "payinvestigator"
  db_user = "payinvestigator"
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.app_name}-rds"
  subnet_ids = data.aws_subnets.public.ids
}

resource "aws_security_group" "rds" {
  name   = "${var.app_name}-rds"
  vpc_id = data.aws_vpc.default.id
}

resource "aws_security_group_rule" "rds_ingress_lambda" {
  type                     = "ingress"
  security_group_id        = aws_security_group.rds.id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda_ingest.id
}

resource "aws_security_group_rule" "rds_ingress_public" {
  type              = "ingress"
  security_group_id = aws_security_group.rds.id
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_security_group_rule" "rds_ingress_backend" {
  type                     = "ingress"
  security_group_id        = aws_security_group.rds.id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.backend_task.id
}

resource "aws_security_group_rule" "lambda_egress_rds" {
  type                     = "egress"
  security_group_id        = aws_security_group.lambda_ingest.id
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.rds.id
}

resource "aws_security_group_rule" "lambda_egress_s3" {
  type              = "egress"
  security_group_id = aws_security_group.lambda_ingest.id
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
}

resource "aws_db_instance" "main" {
  identifier              = "${var.app_name}-postgres"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp2"
  multi_az                = false
  db_name                 = local.db_name
  username                = local.db_user
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = true
  skip_final_snapshot     = true
  backup_retention_period = 0
}

resource "aws_ssm_parameter" "db_url" {
  name  = "/${var.app_name}/db_url"
  type  = "SecureString"
  value = "postgresql://${local.db_user}:${random_password.db.result}@${aws_db_instance.main.endpoint}/${local.db_name}"
}

resource "aws_ssm_parameter" "langsmith_api_key" {
  name  = "/${var.app_name}/langsmith_api_key"
  type  = "SecureString"
  value = var.langsmith_api_key != "" ? var.langsmith_api_key : "unset"
}
