resource "aws_s3_bucket" "mockdata" {
  bucket        = "${var.app_name}-mockdata-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "mockdata" {
  bucket = aws_s3_bucket.mockdata.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "knowledge_base" {
  bucket        = "${var.app_name}-kb-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "knowledge_base" {
  bucket = aws_s3_bucket.knowledge_base.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "knowledge_base_docs" {
  for_each = fileset("${path.module}/assets", "*.md")

  bucket       = aws_s3_bucket.knowledge_base.id
  key          = "assets/${each.value}"
  source       = "${path.module}/assets/${each.value}"
  source_hash  = filemd5("${path.module}/assets/${each.value}")
  content_type = "text/markdown"
}

resource "aws_s3_bucket" "frontend" {
  bucket        = "${var.app_name}-frontend-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "frontend_public" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject"]
      Resource  = ["${aws_s3_bucket.frontend.arn}/*"]
    }]
  })

  depends_on = [
    aws_s3_bucket_public_access_block.frontend,
    aws_s3_bucket_ownership_controls.frontend,
  ]
}
