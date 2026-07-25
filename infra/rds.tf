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
  engine_version                  = "16.11"
  engine_mode                     = "provisioned"
  enable_http_endpoint            = true
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

resource "aws_secretsmanager_secret" "db_credentials" {
  name                    = "${var.app_name}/aurora-master"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    engine              = "aurora-postgresql"
    host                = aws_rds_cluster.main.endpoint
    port                = 5432
    dbname              = local.db_name
    username            = local.db_user
    password            = random_password.db.result
    dbClusterIdentifier = aws_rds_cluster.main.cluster_identifier
  })
}

resource "aws_ssm_parameter" "langsmith_api_key" {
  name  = "/${var.app_name}/langsmith_api_key"
  type  = "SecureString"
  value = var.langsmith_api_key != "" ? var.langsmith_api_key : "unset"
}
