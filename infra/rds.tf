resource "random_password" "db" {
  length  = 24
  special = false
}

locals {
  db_name = "payinvestigator"
  db_user = "payinvestigator"
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.app_name}-aurora"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_rds_cluster" "main" {
  cluster_identifier              = "${var.app_name}-aurora"
  engine                          = "aurora-postgresql"
  engine_version                  = "16.3"
  engine_mode                     = "provisioned"
  database_name                   = local.db_name
  master_username                 = local.db_user
  master_password                 = random_password.db.result
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  storage_encrypted               = true
  skip_final_snapshot             = true
  deletion_protection             = false
  backup_retention_period         = 1
  apply_immediately               = true
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 2
    seconds_until_auto_pause = 600
  }
}

resource "aws_rds_cluster_instance" "main" {
  identifier           = "${var.app_name}-aurora-1"
  cluster_identifier   = aws_rds_cluster.main.id
  instance_class       = "db.serverless"
  engine               = aws_rds_cluster.main.engine
  engine_version       = aws_rds_cluster.main.engine_version
  db_subnet_group_name = aws_db_subnet_group.main.name
  publicly_accessible  = false
}

resource "aws_ssm_parameter" "db_url" {
  name  = "/${var.app_name}/db_url"
  type  = "SecureString"
  value = "postgresql://${local.db_user}:${random_password.db.result}@${aws_rds_cluster.main.endpoint}/${local.db_name}"
}

resource "aws_ssm_parameter" "langsmith_api_key" {
  name  = "/${var.app_name}/langsmith_api_key"
  type  = "SecureString"
  value = var.langsmith_api_key != "" ? var.langsmith_api_key : "unset"
}
