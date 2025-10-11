# Agent Development Guidelines

This document provides guidance for AI agents working on this AWS document processing infrastructure project.

## Project Overview

This is a serverless document processing pipeline built on AWS that:
- Processes documents uploaded to S3 `/incoming` folder
- Uses Amazon Textract for OCR
- Applies LLM analysis with OpenAI
- Moves processed files to `/complete` folder
- Stores results in `/results` folder

## Technology Stack

- **Infrastructure**: Terraform (HCL)
- **Compute**: AWS Lambda (Python 3.11)
- **Storage**: S3, DynamoDB
- **Orchestration**: Step Functions
- **Messaging**: SQS
- **AI/ML**: Amazon Textract, OpenAI API

## Agent Development Guidelines

### Python Development

#### Lambda Functions
- **Location**: All Lambda code in `/lambda/` directory
- **Runtime**: Python 3.11
- **Dependencies**: Use built-in libraries when possible (urllib instead of requests)
- **Error Handling**: Always include try/catch blocks with proper logging
- **Timeout**: OCR Lambda needs 300+ seconds for large files
- **Memory**: LLM Lambda needs 512MB for OpenAI processing

#### Code Standards
```python
# Always include proper imports
import os
import json
import boto3
from datetime import datetime

# Use environment variables for configuration
bucket_name = os.environ['BUCKET_NAME']
table_name = os.environ['DOCUMENTS_TABLE']

# Include comprehensive error handling
try:
    # Main logic here
    pass
except Exception as e:
    print(f"Error in function: {str(e)}")
    # Update DynamoDB with error status
    raise
```

#### Key Lambda Functions
1. **ingest_handler.py**: Processes S3 events from SQS, triggers Step Functions
2. **ocr_handler.py**: Uses Textract for document OCR, saves text to S3
3. **aggregator_handler.py**: Combines OCR text from multiple pages
4. **llm_handler.py**: OpenAI analysis with schema-based extraction

### Terraform Development

#### Infrastructure as Code
- **Main Configuration**: `main.tf` - All resources defined here
- **Variables**: `variables.tf` - Input parameters
- **Outputs**: `outputs.tf` - Resource outputs
- **State**: Use remote state with locking (S3 + DynamoDB)

#### Resource Naming Convention
```hcl
# Use consistent naming with project prefix
resource "aws_s3_bucket" "docproc" {
  bucket = "${var.project_name}-bucket-${var.region}"
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.project_name}-ingest"
}
```

#### Key Resources
- **S3 Bucket**: Document storage with folder structure
- **DynamoDB**: Document state tracking
- **SQS Queue**: Event processing with DLQ
- **Lambda Functions**: Processing logic
- **Step Functions**: Workflow orchestration
- **IAM Roles**: Proper permissions for all services

#### Best Practices
```hcl
# Always include tags
tags = {
  Name = "${var.project_name}-resource-name"
  Environment = "production"
}

# Use locals for complex values
locals {
  bucket_name = "${var.project_name}-bucket-${var.region}"
}

# Include proper dependencies
depends_on = [aws_iam_role.lambda_exec]
```

### Schema Development

#### Classification Schema
- **Location**: `sample-schemas/classification.json`
- **Upload**: Must be uploaded to S3 `system-schemas/` folder
- **Validation**: Use enum values for controlled vocabularies
- **Required Fields**: document_type, confidence
- **Optional Fields**: industry, category, primary_company, secondary_company

#### Document Types
- `promotion` - Ads, marketing materials, offers
- `invoice` - Business invoices
- `receipt` - Purchase receipts
- `contract` - Legal contracts
- `letter` - Business/personal letters
- `other` - Unknown document types

### AWS Service Integration

#### S3 Event Processing
- **Trigger**: S3 ObjectCreated events
- **Filter**: Only `incoming/` folder
- **Target**: SQS queue for reliability
- **Delay**: 10-15 seconds for notifications

#### Step Functions Workflow
- **States**: OCR → CheckOCRComplete → WaitForOCR → AggregateText → LLM
- **Retry Logic**: Built-in retry with exponential backoff
- **Error Handling**: Failed executions go to DLQ

#### DynamoDB Schema
```json
{
  "document_id": "string (hash key)",
  "status": "string (AWAITING_PAGES | OCR_RUNNING | AGGREGATING | LLM_RUNNING | COMPLETE | FAILED)",
  "pages": ["array of S3 keys"],
  "textract_jobs": {"object with job IDs"},
  "ocr_text_keys": ["array of text file keys"],
  "combined_key": "string (S3 key for combined text)",
  "result_key": "string (S3 key for results)",
  "created_at": "ISO timestamp",
  "updated_at": "ISO timestamp"
}
```

## Common Issues and Solutions

### S3 Notifications Not Working
- **Symptom**: Files uploaded but no processing triggered
- **Solution**: Recreate S3 notification configuration
- **Command**: `aws s3api put-bucket-notification-configuration`

### Lambda Timeouts
- **Symptom**: OCR processing fails with timeout
- **Solution**: Increase timeout to 300+ seconds
- **Command**: `aws lambda update-function-configuration --timeout 300`

### Missing Dependencies
- **Symptom**: Import errors in Lambda
- **Solution**: Use built-in libraries or create deployment package
- **Note**: Prefer urllib over requests for HTTP calls

### Step Functions Infinite Loops
- **Symptom**: Execution stuck in WaitForOCR state
- **Solution**: Ensure Lambda returns proper status and preserves context
- **Fix**: Include `pages` and `bucket_name` in Lambda responses

## Testing and Debugging

### Manual Testing
```bash
# Upload test file
aws s3 cp test-file.jpg s3://bucket-name/incoming/test-doc_1.jpg

# Check DynamoDB status
aws dynamodb get-item --table-name table-name --key '{"document_id": {"S": "test-doc"}}'

# Check Step Functions execution
aws stepfunctions describe-execution --execution-arn arn:aws:states:...
```

### Log Analysis
```bash
# Check Lambda logs
aws logs describe-log-streams --log-group-name /aws/lambda/function-name
aws logs get-log-events --log-group-name /aws/lambda/function-name --log-stream-name stream-name
```

## Documentation Requirements

**IMPORTANT**: All major functionality should be documented with a file in the `/docs` folder.

### Required Documentation
- **Architecture**: System design and component interactions
- **API Reference**: Lambda function interfaces and schemas
- **Deployment Guide**: Step-by-step infrastructure setup
- **Troubleshooting**: Common issues and solutions
- **Schema Reference**: Document classification schemas and validation rules

### Documentation Standards
- Use Markdown format
- Include code examples
- Provide clear step-by-step instructions
- Include troubleshooting sections
- Keep documentation up-to-date with code changes

## Security Considerations

- **IAM Roles**: Principle of least privilege
- **Secrets**: Use AWS Secrets Manager for API keys
- **Encryption**: Enable S3 server-side encryption
- **VPC**: Consider VPC endpoints for production
- **Monitoring**: Enable CloudWatch logging and metrics

## Performance Optimization

- **Lambda Memory**: Adjust based on workload (OCR: 128MB, LLM: 512MB)
- **Concurrency**: Step Functions can process multiple documents in parallel
- **S3 Transfer**: Use multipart uploads for large files
- **DynamoDB**: Use on-demand billing for variable workloads

## Monitoring and Alerting

- **CloudWatch Logs**: All Lambda functions log to CloudWatch
- **Step Functions**: Monitor execution success/failure rates
- **SQS**: Monitor queue depth and DLQ messages
- **DynamoDB**: Track read/write capacity and throttling

---

*This document should be updated as the system evolves. All changes should be reflected in the `/docs` folder.*
