resource "aws_security_group" "rds" {
  name   = "${var.app_name}-aurora"
  vpc_id = data.aws_vpc.default.id
}
