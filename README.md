# AWS Document Processing Infrastructure

A serverless document processing pipeline built on AWS that automatically processes documents uploaded to S3, performs OCR using Amazon Textract, and applies LLM analysis with OpenAI.

## 🏗️ Architecture

```
S3 (incoming) → SQS → Lambda (Ingest) → Step Functions → Lambda (OCR) → Lambda (Aggregator) → Lambda (LLM) → S3 (complete/results)
```

## 🚀 Features

- **Automatic Processing**: Upload files to `/incoming` folder for automatic processing
- **OCR with Textract**: Extracts text from images and PDFs
- **LLM Analysis**: OpenAI-powered document classification and data extraction
- **Scalable**: Handles thousands of documents in parallel
- **Reliable**: SQS-based architecture with retry capabilities and dead letter queues
- **File Management**: Automatically moves processed files to `/complete` folder

## 📁 S3 Folder Structure

- **`/incoming/`**: Upload documents here for processing
- **`/staging/`**: OCR text files and combined text
- **`/results/`**: LLM analysis results (JSON)
- **`/complete/`**: Processed original files
- **`/system-schemas/`**: Document classification schemas

## 🛠️ Technology Stack

- **Infrastructure**: Terraform
- **Compute**: AWS Lambda (Python 3.11)
- **Storage**: S3, DynamoDB
- **Orchestration**: Step Functions
- **Messaging**: SQS
- **AI/ML**: Amazon Textract, OpenAI API

## 📋 Document Classification

The system classifies documents into these types:
- **`promotion`**: Ads, marketing materials, offers
- **`invoice`**: Business invoices
- **`receipt`**: Purchase receipts
- **`contract`**: Legal contracts
- **`letter`**: Business/personal letters
- **`other`**: Unknown document types

### Industry Classification
- Credit Card, Banking, Insurance, Investment, Mortgage & Loans
- Retail, Shipping, Technology, Telecoms, Travel, Auto, Tobacco

## 🚀 Quick Start

### Prerequisites
- AWS CLI configured
- Terraform installed
- OpenAI API key

### Deployment

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd aws-doc-processing-infra
   ```

2. **Configure variables**
   ```bash
   cp variables.tf.example variables.tf
   # Edit variables.tf with your values
   ```

3. **Deploy infrastructure**
   ```bash
   terraform init
   terraform plan
   terraform apply
   ```

4. **Set OpenAI API key**
   ```bash
   aws secretsmanager update-secret \
     --secret-id docproc-openai-key \
     --secret-string '{"OPENAI_API_KEY":"your-api-key-here"}'
   ```

### Usage

1. **Upload a document**
   ```bash
   aws s3 cp your-document.jpg s3://your-bucket/incoming/document-name_1.jpg
   ```

2. **Check processing status**
   ```bash
   aws dynamodb get-item \
     --table-name docproc-documents \
     --key '{"document_id": {"S": "document-name"}}'
   ```

3. **View results**
   ```bash
   aws s3 cp s3://your-bucket/results/document-name_response.json -
   ```

## 📊 Example Output

```json
{
  "document_analysis": {
    "classification": {
      "success": true,
      "data": {
        "document_type": "promotion",
        "confidence": 0.8,
        "industry": "Banking",
        "category": "Credit Cards",
        "primary_company": "Cash App",
        "secondary_company": null,
        "key_entities": ["Cash App", "Sutton Bank"],
        "amount_found": "$100.00"
      }
    }
  }
}
```

## 🔧 Configuration

### Environment Variables
- `BUCKET_NAME`: S3 bucket for document storage
- `DOCUMENTS_TABLE`: DynamoDB table for document tracking
- `OPENAI_SECRET_NAME`: AWS Secrets Manager secret name for OpenAI API key
- `STEP_FUNCTION_ARN`: Step Functions state machine ARN

### Lambda Functions
- **ingest**: Processes S3 events, triggers Step Functions
- **ocr**: Performs OCR using Amazon Textract
- **aggregator**: Combines OCR text from multiple pages
- **llm**: OpenAI analysis with schema-based extraction

## 📚 Documentation

- **[AGENT.md](AGENT.md)**: Comprehensive development guidelines for AI agents
- **[docs/](docs/)**: Detailed documentation for major functionality
- **[sample-schemas/](sample-schemas/)**: Document classification schemas

## 🔍 Monitoring

- **CloudWatch Logs**: All Lambda functions log to CloudWatch
- **Step Functions**: Monitor execution success/failure rates
- **SQS**: Monitor queue depth and dead letter queue messages
- **DynamoDB**: Track document processing status

## 🛡️ Security

- IAM roles with least privilege access
- Secrets stored in AWS Secrets Manager
- S3 server-side encryption enabled
- VPC endpoints for production deployments

## 🚨 Troubleshooting

### Common Issues

1. **Files not processing**
   - Check S3 notification configuration
   - Verify SQS queue has messages
   - Check Lambda function logs

2. **OCR timeouts**
   - Increase Lambda timeout to 300+ seconds
   - Check file size and format

3. **Missing dependencies**
   - Use built-in libraries (urllib instead of requests)
   - Create deployment package if needed

See [AGENT.md](AGENT.md) for detailed troubleshooting guide.

## 🤝 Contributing

1. Follow the guidelines in [AGENT.md](AGENT.md)
2. Document all major functionality in the `/docs` folder
3. Test changes thoroughly
4. Update documentation as needed

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues and questions:
1. Check the troubleshooting section in [AGENT.md](AGENT.md)
2. Review CloudWatch logs for error details
3. Check DynamoDB document status for processing state
