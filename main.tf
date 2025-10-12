# Main Terraform configuration file
# Contains S3, Lambdas, Step Functions, IAM, and other resources
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  bucket_name       = "${var.project_name}-bucket"
  queue_name        = "${var.project_name}-queue"
  table_name        = "${var.project_name}-documents"
  state_machine_name = "${var.project_name}-workflow"
  secret_name       = "${var.project_name}-openai-key"
}

# -----------------------
# S3 bucket
# -----------------------
resource "aws_s3_bucket" "docproc" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "block" {
  bucket                  = aws_s3_bucket.docproc.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Optional folder objects (prefixes)
resource "aws_s3_object" "folders" {
  for_each = toset(["incoming/", "staging/", "complete/", "results/", "system-schemas/"])
  bucket   = aws_s3_bucket.docproc.id
  key      = each.key
}

# -----------------------
# DynamoDB table
# -----------------------
resource "aws_dynamodb_table" "documents" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "document_id"

  attribute {
    name = "document_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  # Global Secondary Index for querying by status
  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  # TTL for automatic cleanup of old records
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "${var.project_name}-documents"
  }
}

# -----------------------
# SQS queues
# -----------------------
# Dead Letter Queue for failed messages
resource "aws_sqs_queue" "dlq" {
  name = "${local.queue_name}-dlq"
  
  message_retention_seconds = 1209600 # 14 days
  
  tags = {
    Name = "${var.project_name}-dlq"
  }
}

# Main processing queue
resource "aws_sqs_queue" "incoming" {
  name = local.queue_name
  
  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
  
  # Visibility timeout for Lambda processing
  visibility_timeout_seconds = 60
  
  # Message retention
  message_retention_seconds = 345600 # 4 days
  
  tags = {
    Name = "${var.project_name}-queue"
  }
}

data "aws_iam_policy_document" "sqs_policy" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    actions   = ["SQS:SendMessage"]
    resources = [aws_sqs_queue.incoming.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.docproc.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "policy" {
  queue_url = aws_sqs_queue.incoming.id
  policy    = data.aws_iam_policy_document.sqs_policy.json
}

# S3 → SQS notification
resource "aws_s3_bucket_notification" "notify" {
  bucket = aws_s3_bucket.docproc.id

  queue {
    queue_arn     = aws_sqs_queue.incoming.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
  }

  depends_on = [aws_sqs_queue_policy.policy]
}

# -----------------------
# IAM Role for Lambda
# -----------------------
data "aws_iam_policy_document" "assume_role" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "${var.project_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

# Permissions: CloudWatch, S3, DynamoDB, SQS, StepFunctions
resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy_attachment" "s3" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}
resource "aws_iam_role_policy_attachment" "ddb" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}
resource "aws_iam_role_policy_attachment" "sqs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSQSFullAccess"
}
resource "aws_iam_role_policy_attachment" "step" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AWSStepFunctionsFullAccess"
}

# -----------------------
# Secrets Manager for OpenAI Key
# -----------------------
resource "aws_secretsmanager_secret" "openai" {
  name = local.secret_name
}

resource "aws_secretsmanager_secret_version" "openai_version" {
  secret_id     = aws_secretsmanager_secret.openai.id
  secret_string = jsonencode({ "OPENAI_API_KEY" = var.openai_secret_value })
}

data "aws_iam_policy_document" "secrets_access" {
  statement {
    effect = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.openai.arn]
  }
}

resource "aws_iam_policy" "lambda_secrets_policy" {
  name   = "${var.project_name}-lambda-secrets"
  policy = data.aws_iam_policy_document.secrets_access.json
}

resource "aws_iam_role_policy_attachment" "secrets_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_secrets_policy.arn
}

# Textract permissions for OCR Lambda
resource "aws_iam_role_policy_attachment" "textract" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonTextractFullAccess"
}

# Step Functions permissions for ingest Lambda
data "aws_iam_policy_document" "step_functions_policy" {
  statement {
    effect = "Allow"
    actions = [
      "states:StartExecution"
    ]
    resources = [
      aws_sfn_state_machine.workflow.arn
    ]
  }
}

resource "aws_iam_policy" "step_functions_policy" {
  name   = "${var.project_name}-step-functions"
  policy = data.aws_iam_policy_document.step_functions_policy.json
}

resource "aws_iam_role_policy_attachment" "step_functions_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.step_functions_policy.arn
}

# -----------------------
# Lambda Packaging Helpers
# -----------------------
data "archive_file" "ingest_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/ingest_handler.py"
  output_path = "${path.module}/lambda/ingest_handler.zip"
}

data "archive_file" "ocr_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/ocr_handler.py"
  output_path = "${path.module}/lambda/ocr_handler.zip"
}

data "archive_file" "aggregator_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/aggregator_handler.py"
  output_path = "${path.module}/lambda/aggregator_handler.zip"
}

data "archive_file" "llm_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/llm_handler.py"
  output_path = "${path.module}/lambda/llm_handler.zip"
}

# -----------------------
# Lambda Functions
# -----------------------
resource "aws_lambda_function" "ingest" {
  function_name = "${var.project_name}-ingest"
  filename      = data.archive_file.ingest_zip.output_path
  handler       = "ingest_handler.lambda_handler"
  runtime       = "python3.11"
  role          = aws_iam_role.lambda_exec.arn

  environment {
    variables = {
      BUCKET_NAME        = aws_s3_bucket.docproc.bucket
      DOCUMENTS_TABLE    = aws_dynamodb_table.documents.name
      OPENAI_SECRET_NAME = aws_secretsmanager_secret.openai.name
      STEP_FUNCTION_ARN  = aws_sfn_state_machine.workflow.arn
      REGION             = var.region
    }
  }
}

resource "aws_lambda_function" "ocr" {
  function_name = "${var.project_name}-ocr"
  filename      = data.archive_file.ocr_zip.output_path
  handler       = "ocr_handler.lambda_handler"
  runtime       = "python3.11"
  role          = aws_iam_role.lambda_exec.arn

  environment {
    variables = {
      BUCKET_NAME        = aws_s3_bucket.docproc.bucket
      DOCUMENTS_TABLE    = aws_dynamodb_table.documents.name
      OPENAI_SECRET_NAME = aws_secretsmanager_secret.openai.name
      REGION             = var.region
    }
  }
}

resource "aws_lambda_function" "aggregator" {
  function_name = "${var.project_name}-aggregator"
  filename      = data.archive_file.aggregator_zip.output_path
  handler       = "aggregator_handler.lambda_handler"
  runtime       = "python3.11"
  role          = aws_iam_role.lambda_exec.arn
  timeout       = 60
  memory_size   = 256

  environment {
    variables = {
      BUCKET_NAME        = aws_s3_bucket.docproc.bucket
      DOCUMENTS_TABLE    = aws_dynamodb_table.documents.name
      REGION             = var.region
    }
  }
}

resource "aws_lambda_function" "llm" {
  function_name = "${var.project_name}-llm"
  filename      = data.archive_file.llm_zip.output_path
  handler       = "llm_handler.lambda_handler"
  runtime       = "python3.11"
  role          = aws_iam_role.lambda_exec.arn
  timeout       = 300
  memory_size   = 512

  environment {
    variables = {
      BUCKET_NAME        = aws_s3_bucket.docproc.bucket
      DOCUMENTS_TABLE    = aws_dynamodb_table.documents.name
      OPENAI_SECRET_NAME = aws_secretsmanager_secret.openai.name
      REGION             = var.region
    }
  }
}

# Allow SQS to trigger ingest lambda
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.incoming.arn
  function_name    = aws_lambda_function.ingest.arn
  batch_size       = 10
  enabled          = true
}

# -----------------------
# Step Functions Workflow
# -----------------------
data "aws_iam_policy_document" "step_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "step_role" {
  name               = "${var.project_name}-step-role"
  assume_role_policy = data.aws_iam_policy_document.step_trust.json
}

resource "aws_iam_role_policy_attachment" "step_policy" {
  role       = aws_iam_role.step_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSStepFunctionsFullAccess"
}

# Allow Step Functions to invoke Lambda functions
data "aws_iam_policy_document" "step_lambda_policy" {
  statement {
    effect = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.ingest.arn,
      aws_lambda_function.ocr.arn,
      aws_lambda_function.aggregator.arn,
      aws_lambda_function.llm.arn
    ]
  }
}

resource "aws_iam_policy" "step_lambda_policy" {
  name   = "${var.project_name}-step-lambda"
  policy = data.aws_iam_policy_document.step_lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "step_lambda_attach" {
  role       = aws_iam_role.step_role.name
  policy_arn = aws_iam_policy.step_lambda_policy.arn
}

locals {
  step_def = jsonencode({
    Comment = "Document Processing Workflow"
    StartAt = "OCR"
    States = {
      OCR = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        OutputPath = "$.Payload"
        Parameters = {
          FunctionName = aws_lambda_function.ocr.arn
          "Payload.$"  = "$"
        }
        Next = "CheckOCRComplete"
        Retry = [
          {
            ErrorEquals = ["States.ALL"]
            IntervalSeconds = 30
            MaxAttempts = 3
            BackoffRate = 2.0
          }
        ]
      }
      CheckOCRComplete = {
        Type = "Choice"
        Choices = [
          {
            Variable = "$.status"
            StringEquals = "ocr_complete"
            Next = "AggregateText"
          }
        ]
        Default = "WaitForOCR"
      }
      WaitForOCR = {
        Type = "Wait"
        Seconds = 30
        Next = "OCR"
      }
      AggregateText = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        OutputPath = "$.Payload"
        Parameters = {
          FunctionName = aws_lambda_function.aggregator.arn
          "Payload.$"  = "$"
        }
        Next = "LLM"
        Retry = [
          {
            ErrorEquals = ["States.ALL"]
            IntervalSeconds = 10
            MaxAttempts = 3
            BackoffRate = 2.0
          }
        ]
      }
      LLM = {
        Type       = "Task"
        Resource   = "arn:aws:states:::lambda:invoke"
        OutputPath = "$.Payload"
        Parameters = {
          FunctionName = aws_lambda_function.llm.arn
          "Payload.$"  = "$"
        }
        End = true
        Retry = [
          {
            ErrorEquals = ["States.ALL"]
            IntervalSeconds = 10
            MaxAttempts = 3
            BackoffRate = 2.0
          }
        ]
      }
    }
  })
}

resource "aws_sfn_state_machine" "workflow" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.step_role.arn
  definition = local.step_def
}

