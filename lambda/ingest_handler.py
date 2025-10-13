# Python Lambda function for document ingestion
import os
import json
import boto3
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import unquote_plus

def lambda_handler(event, context):
    """
    Processes S3 events and manages document state in DynamoDB.
    
    Expected S3 event structure:
    {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "bucket-name"},
                    "object": {"key": "incoming/abcxyz_1.jpg"}
                }
            }
        ]
    }
    """
    print("Ingest Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        dynamodb = boto3.resource('dynamodb')
        stepfunctions = boto3.client('stepfunctions')
        
        bucket_name = os.environ['BUCKET_NAME']
        table_name = os.environ['DOCUMENTS_TABLE']
        state_machine_arn = os.environ['STEP_FUNCTION_ARN']
        table = dynamodb.Table(table_name)
        
        processed_documents = []
        
        # Process each SQS record
        for record in event.get('Records', []):
            if record.get('eventSource') != 'aws:sqs':
                continue
                
            # Parse S3 event from SQS message body
            try:
                s3_event = json.loads(record['body'])
            except json.JSONDecodeError:
                print(f"Failed to parse SQS message body: {record['body']}")
                continue
                
            # Process each S3 record in the parsed event
            for s3_record in s3_event.get('Records', []):
                if s3_record.get('eventSource') != 'aws:s3':
                    continue
                    
                # Extract S3 object information
                bucket = s3_record['s3']['bucket']['name']
                key = unquote_plus(s3_record['s3']['object']['key'])
                
                # Skip if not in incoming folder
                if not key.startswith('incoming/'):
                    continue
                
                # Extract filename information
                # Support both formats: filename.ext OR filename_page.ext OR filename-page.ext
                filename = key.split('/')[-1]
                
                # Try to match filename with page number first
                match = re.match(r'^(.+?)[_-](\d+)\.(.+)$', filename)
                if match:
                    # Filename with page number: document_1.jpg -> document, 1, jpg
                    base_filename = match.group(1)
                    page_number = int(match.group(2))
                    file_extension = match.group(3)
                else:
                    # Try to match filename without page number: document.jpg -> document, 1, jpg
                    match = re.match(r'^(.+)\.(.+)$', filename)
                    if match:
                        base_filename = match.group(1)
                        page_number = 1  # Default to page 1 for single files
                        file_extension = match.group(2)
                    else:
                        print(f"Skipping file with unexpected format: {filename}")
                        continue
                
                # Generate unique document ID using pure GUID
                document_id = str(uuid.uuid4())
                print(f"Generated document ID: {document_id} for base filename: {base_filename}")
                
                # Validate file type (extension)
                if file_extension.lower() not in ['jpg', 'jpeg', 'png', 'pdf']:
                    print(f"Skipping unsupported file type: {file_extension}")
                    continue
                
                # Validate MIME type to prevent Textract failures
                try:
                    response = s3.head_object(Bucket=bucket, Key=key)
                    content_type = response.get('ContentType', '')
                    
                    # Validate MIME type
                    valid_mime_types = ['image/jpeg', 'image/png', 'application/pdf']
                    if content_type not in valid_mime_types:
                        print(f"Skipping file with invalid MIME type: {content_type} for {filename}")
                        continue
                        
                    print(f"Validated MIME type: {content_type} for {filename}")
                    
                except Exception as e:
                    print(f"Error validating MIME type for {filename}: {str(e)}")
                    continue
                
                print(f"Processing document: {document_id}, page: {page_number}")
                
                # Get or create document record
                try:
                    response = table.get_item(Key={'document_id': document_id})
                    if 'Item' in response:
                        doc_item = response['Item']
                        status = doc_item.get('status', 'AWAITING_PAGES')
                    else:
                        # Create new document record
                        doc_item = {
                            'document_id': document_id,
                            'original_filename': filename,  # Store full original filename
                            'status': 'AWAITING_PAGES',
                            'pages_received': 0,
                            'pages': [],
                            'textract_jobs': {},
                            'ocr_text_keys': [],
                            'retries': 0,
                            'created_at': datetime.utcnow().isoformat(),
                            'updated_at': datetime.utcnow().isoformat(),
                            'ttl': int((datetime.utcnow() + timedelta(days=30)).timestamp())
                        }
                        status = 'AWAITING_PAGES'
                except Exception as e:
                    print(f"Error accessing DynamoDB: {str(e)}")
                    raise
                
                # Update document with new page
                pages = doc_item.get('pages', [])
                if key not in pages:
                    pages.append(key)
                
                # Update DynamoDB record
                update_expression = "SET pages = :pages, pages_received = :count, updated_at = :timestamp"
                expression_values = {
                    ':pages': pages,
                    ':count': len(pages),
                    ':timestamp': datetime.utcnow().isoformat()
                }
                
                # If this is the first page, set initial status and original_filename
                if len(pages) == 1:
                    update_expression += ", #status = :status, original_filename = :filename"
                    expression_values[':status'] = 'AWAITING_PAGES'
                    expression_values[':filename'] = filename
                
                table.update_item(
                    Key={'document_id': document_id},
                    UpdateExpression=update_expression,
                    ExpressionAttributeNames={'#status': 'status'},
                    ExpressionAttributeValues=expression_values
                )
                
                # Check if we should start processing
                # For now, start processing after first page (can be enhanced with expected_pages)
                if len(pages) == 1 and status == 'AWAITING_PAGES':
                    # Start Step Functions execution
                    execution_input = {
                        'document_id': document_id,
                        'pages': pages,
                        'status': 'OCR_RUNNING'
                    }
                    
                    try:
                        execution_response = stepfunctions.start_execution(
                            stateMachineArn=state_machine_arn,
                            name=f"{document_id}-{int(datetime.utcnow().timestamp())}",
                            input=json.dumps(execution_input)
                        )
                        
                        # Update document status
                        table.update_item(
                            Key={'document_id': document_id},
                            UpdateExpression="SET #status = :status, step_function_execution_arn = :arn, updated_at = :timestamp",
                            ExpressionAttributeNames={'#status': 'status'},
                            ExpressionAttributeValues={
                                ':status': 'OCR_RUNNING',
                                ':arn': execution_response['executionArn'],
                                ':timestamp': datetime.utcnow().isoformat()
                            }
                        )
                        
                        print(f"Started Step Functions execution for document {document_id}")
                        processed_documents.append(document_id)
                        
                    except Exception as e:
                        print(f"Error starting Step Functions: {str(e)}")
                        # Update status to failed
                        table.update_item(
                            Key={'document_id': document_id},
                            UpdateExpression="SET #status = :status, last_error = :error, updated_at = :timestamp",
                            ExpressionAttributeNames={'#status': 'status'},
                            ExpressionAttributeValues={
                                ':status': 'FAILED',
                                ':error': str(e),
                                ':timestamp': datetime.utcnow().isoformat()
                            }
                        )
                        raise
        
        return {
            'statusCode': 200,
            'processed_documents': processed_documents,
            'status': 'success'
        }
        
    except Exception as e:
        print(f"Error in ingest handler: {str(e)}")
        raise