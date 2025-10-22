# Python Lambda function for document ingestion with proper multi-page document processing
import os
import json
import boto3
import re
import time
import uuid
from datetime import datetime
from botocore.exceptions import ClientError

def lambda_handler(event, context):
    """
    Processes S3 events for document ingestion with proper multi-page document processing.
    
    For multi-page documents, waits 3 seconds to collect all files with the same base name,
    then processes them together as a single document.
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
        
        for record in event['Records']:
            # Parse S3 event
            s3_event = json.loads(record['body'])
            s3_record = s3_event['Records'][0]
            s3_object = s3_record['s3']['object']
            filename = s3_object['key']
            
            print(f"Processing file: {filename}")
            
            # Extract base filename (remove page number and extension)
            base_filename = extract_base_filename(filename)
            print(f"Base filename: {base_filename}")
            
            # Validate file type
            if not validate_file_type(s3, bucket_name, filename):
                print(f"Skipping file with invalid type: {filename}")
                continue
            
            # Check if a document with this base filename already exists
            existing_document = find_existing_document(table, base_filename)
            if existing_document:
                print(f"Document with base filename '{base_filename}' already exists: {existing_document['document_id']}")
                print(f"Skipping processing to avoid duplicate documents")
                continue
            
            # Get all files with the same base name
            all_files = get_files_with_base_name(s3, bucket_name, base_filename)
            print(f"Found {len(all_files)} files with base name '{base_filename}': {all_files}")
            
            if len(all_files) == 1:
                # Single file - process immediately
                print(f"Single file detected - processing immediately: {filename}")
                process_document(s3, bucket_name, table, stepfunctions, state_machine_arn, all_files, base_filename)
                processed_documents.append(filename)
            else:
                # Multiple files - wait 3 seconds for more files to arrive
                print(f"Multiple files detected - waiting 3 seconds for more files...")
                time.sleep(3)
                
                # Check again for any additional files
                final_files = get_files_with_base_name(s3, bucket_name, base_filename)
                print(f"After 3-second wait, found {len(final_files)} files: {final_files}")
                
                # Process all files together as a single document
                process_document(s3, bucket_name, table, stepfunctions, state_machine_arn, final_files, base_filename)
                processed_documents.extend(final_files)
        
        return {
            'statusCode': 200,
            'processed_documents': processed_documents,
            'message': f'Processed {len(processed_documents)} documents'
        }
        
    except Exception as e:
        print(f"Error in ingest handler: {str(e)}")
        raise

def find_existing_document(table, base_filename):
    """Find existing document with the same base filename."""
    try:
        # Scan for documents with matching original_filename
        response = table.scan(
            FilterExpression='original_filename = :base_filename',
            ExpressionAttributeValues={
                ':base_filename': base_filename
            }
        )
        
        if response['Items']:
            return response['Items'][0]  # Return the first match
        return None
        
    except Exception as e:
        print(f"Error finding existing document for '{base_filename}': {str(e)}")
        return None

def extract_base_filename(filename):
    """Extract base filename by removing page number and extension."""
    # Remove path
    basename = os.path.basename(filename)
    
    # Pattern to match: basename-page.ext or basename_page.ext
    pattern = r'^(.+?)[_-](\d+)\.(.+)$'
    match = re.match(pattern, basename)
    
    if match:
        return match.group(1)
    else:
        # If no page number pattern, return filename without extension
        return os.path.splitext(basename)[0]

def validate_file_type(s3, bucket_name, filename):
    """Validate that the file is a supported image type."""
    try:
        response = s3.head_object(Bucket=bucket_name, Key=filename)
        content_type = response.get('ContentType', '')
        
        supported_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/tiff', 'image/tif']
        
        if content_type in supported_types:
            print(f"Validated MIME type: {content_type} for {filename}")
            return True
        else:
            print(f"Skipping file with invalid MIME type: {content_type}")
            return False
            
    except Exception as e:
        print(f"Error validating file type for {filename}: {str(e)}")
        return False

def get_files_with_base_name(s3, bucket_name, base_name):
    """Get all files in the incoming folder with the same base name."""
    try:
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix='incoming/',
            Delimiter='/'
        )
        
        matching_files = []
        
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                if key.startswith('incoming/') and key != 'incoming/':
                    filename = os.path.basename(key)
                    file_base_name = extract_base_filename(filename)
                    
                    if file_base_name == base_name:
                        matching_files.append(key)
        
        return sorted(matching_files)
        
    except Exception as e:
        print(f"Error listing files with base name '{base_name}': {str(e)}")
        return []

def process_document(s3, bucket_name, table, stepfunctions, state_machine_arn, files, base_filename):
    """Process a document with the given files."""
    if not files:
        print("No files to process")
        return
    
    # Generate proper document ID (UUID format like before)
    document_id = str(uuid.uuid4())
    
    print(f"Processing document {document_id} with {len(files)} files: {files}")
    
    # Create document record in DynamoDB with all required fields
    table.put_item(
        Item={
            'document_id': document_id,
            'status': 'OCR_RUNNING',
            'pages': files,
            'pages_received': len(files),
            'original_filename': base_filename,  # Add original filename
            'bucket_name': bucket_name,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
    )
    
    # Start Step Functions execution
    execution_input = {
        'document_id': document_id,
        'pages': files,
        'status': 'OCR_RUNNING'
    }
    
    try:
        execution_response = stepfunctions.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"{document_id}-{int(datetime.utcnow().timestamp())}",
            input=json.dumps(execution_input)
        )
        
        # Update document with execution ARN
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET step_function_execution_arn = :arn, updated_at = :timestamp',
            ExpressionAttributeValues={
                ':arn': execution_response['executionArn'],
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        print(f"Started Step Functions execution for document {document_id}: {execution_response['executionArn']}")
        
    except Exception as e:
        print(f"Error starting Step Functions for {document_id}: {str(e)}")
        
        # Update document status to failed
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET #status = :status, last_error = :error, updated_at = :timestamp',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'FAILED',
                ':error': str(e),
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        raise