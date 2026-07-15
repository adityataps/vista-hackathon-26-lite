# ── OpenSearch Serverless (vector store for the Knowledge Base) ───────────────

resource "aws_opensearchserverless_security_policy" "kb_encryption" {
  name = "${var.app_name}-kb-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${var.app_name}-kb"]
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "kb_network" {
  name = "${var.app_name}-kb-net"
  type = "network"
  policy = jsonencode([{
    Rules = [
      { ResourceType = "collection", Resource = ["collection/${var.app_name}-kb"] },
      { ResourceType = "dashboard",  Resource = ["collection/${var.app_name}-kb"] },
    ]
    AllowFromPublic = true
  }])
}

resource "aws_opensearchserverless_access_policy" "kb" {
  name = "${var.app_name}-kb"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.app_name}-kb"]
        Permission   = [
          "aoss:CreateCollectionItems",
          "aoss:DeleteCollectionItems",
          "aoss:UpdateCollectionItems",
          "aoss:DescribeCollectionItems",
        ]
      },
      {
        ResourceType = "index"
        Resource     = ["index/${var.app_name}-kb/*"]
        Permission   = [
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
        ]
      },
    ]
    Principal = [
      aws_iam_role.bedrock_kb.arn,
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root",
    ]
  }])
}

resource "aws_opensearchserverless_collection" "kb" {
  name = "${var.app_name}-kb"
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_security_policy.kb_encryption,
    aws_opensearchserverless_security_policy.kb_network,
  ]
}

# ── IAM role that Bedrock assumes to index and retrieve docs ──────────────────

resource "aws_iam_role" "bedrock_kb" {
  name = "${var.app_name}-bedrock-kb"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb" {
  name = "${var.app_name}-bedrock-kb"
  role = aws_iam_role.bedrock_kb.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.knowledge_base.arn,
          "${aws_s3_bucket.knowledge_base.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        Effect   = "Allow"
        Action   = ["aoss:APIAccessAll"]
        Resource = aws_opensearchserverless_collection.kb.arn
      },
    ]
  })
}

# ── Pre-create the AOSS index that Bedrock expects ────────────────────────────

resource "opensearch_index" "kb_default" {
  name               = "bedrock-knowledge-base-default-index"
  number_of_shards   = "2"
  number_of_replicas = "0"
  index_knn          = true
  mappings = jsonencode({
    properties = {
      "bedrock-knowledge-base-default-vector" = {
        type      = "knn_vector"
        dimension = 1024
        method = {
          engine     = "faiss"
          name       = "hnsw"
          space_type = "l2"
          parameters = { ef_construction = 512, m = 16 }
        }
      }
      "AMAZON_BEDROCK_TEXT_CHUNK" = { type = "text" }
      "AMAZON_BEDROCK_METADATA"   = { type = "text", index = "false" }
    }
  })
  force_destroy = true

  # Bedrock adds its own metadata fields to the mapping after ingestion.
  # Ignoring drift here prevents TF from replacing the index (and wiping vectors) on every apply.
  lifecycle {
    ignore_changes = [mappings]
  }

  depends_on = [
    aws_opensearchserverless_collection.kb,
    aws_opensearchserverless_access_policy.kb,
  ]
}

# ── Bedrock Knowledge Base ─────────────────────────────────────────────────────

resource "aws_bedrockagent_knowledge_base" "main" {
  name     = "${var.app_name}-kb"
  role_arn = aws_iam_role.bedrock_kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.kb.arn
      vector_index_name = "bedrock-knowledge-base-default-index"
      field_mapping {
        vector_field   = "bedrock-knowledge-base-default-vector"
        text_field     = "AMAZON_BEDROCK_TEXT_CHUNK"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  depends_on = [
    aws_opensearchserverless_access_policy.kb,
    opensearch_index.kb_default,
  ]
}

# ── S3 data source ─────────────────────────────────────────────────────────────

resource "aws_bedrockagent_data_source" "kb_s3" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.main.id
  name              = "${var.app_name}-kb-s3"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.knowledge_base.arn
    }
  }
}

# ── Knowledge base documents ───────────────────────────────────────────────────

locals {
  kb_docs = [
    "error-code-catalog.md",
    "iban-format-registry.md",
    "sanctions-screening-procedure.md",
    "duplicate-payment-resolution.md",
    "swift-pacs008-field-guide.md",
    "payment-sla-and-escalation.md",
  ]
}

resource "aws_s3_object" "kb_docs" {
  for_each = toset(local.kb_docs)

  bucket       = aws_s3_bucket.knowledge_base.bucket
  key          = each.value
  source       = "${path.module}/assets/${each.value}"
  content_type = "text/markdown"
  etag         = filemd5("${path.module}/assets/${each.value}")
}
