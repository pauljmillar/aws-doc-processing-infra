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
            has_page_suffix = is_paginated_filename(filename)
            print(f"Base filename: {base_filename} (paginated: {has_page_suffix})")

            # Validate file type
            if not validate_file_type(s3, bucket_name, filename):
                print(f"Skipping file with invalid type: {filename}")
                continue

            # Check if an in-flight (non-terminal) document with this base filename
            # already exists. Terminal (COMPLETE/FAILED) documents don't count - the
            # same base filename can legitimately be re-uploaded and reprocessed later.
            existing_document = find_active_document(table, base_filename)
            if existing_document:
                print(f"Active document with base filename '{base_filename}' already exists: {existing_document['document_id']}")
                print(f"Skipping processing to avoid duplicate documents")
                continue

            # Atomically claim the right to process this base filename. Without this,
            # two pages of the same multi-page document (e.g. tulane_1.jpg and
            # tulane_2.jpg) uploaded together can trigger two concurrent Lambda
            # invocations that both pass the check above before either has written a
            # document record, each creating its own duplicate document/execution for
            # the same pages - or, if one invocation lists S3 before the sibling page
            # has landed, orphaning that page outside of any document entirely.
            if not acquire_processing_lock(table, base_filename):
                print(f"Another invocation is already claiming '{base_filename}', skipping")
                continue

            try:
                if has_page_suffix:
                    # This filename looks like one page of a multi-page document.
                    # Always give sibling pages a window to land in S3 before we
                    # finalize the page list, even if we currently only see this one -
                    # the sibling's upload may simply not have completed yet.
                    print(f"Paginated filename detected - waiting 3 seconds to collect sibling pages...")
                    time.sleep(3)

                all_files = get_files_with_base_name(s3, bucket_name, base_filename)
                print(f"Found {len(all_files)} files with base name '{base_filename}': {all_files}")

                process_document(s3, bucket_name, table, stepfunctions, state_machine_arn, all_files, base_filename)
                processed_documents.extend(all_files)
            finally:
                release_processing_lock(table, base_filename)
        
        return {
            'statusCode': 200,
            'processed_documents': processed_documents,
            'message': f'Processed {len(processed_documents)} documents'
        }
        
    except Exception as e:
        print(f"Error in ingest handler: {str(e)}")
        raise

def find_active_document(table, base_filename):
    """Find a non-terminal (still in-flight) document with the same base filename.

    A document that already reached COMPLETE or FAILED does not block a fresh
    upload of the same base filename - only an in-flight one does, since that
    signals another invocation is (or was very recently) handling these exact
    pages.
    """
    try:
        # Scan for documents with matching original_filename
        response = table.scan(
            FilterExpression='original_filename = :base_filename AND #status <> :complete AND #status <> :failed',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':base_filename': base_filename,
                ':complete': 'COMPLETE',
                ':failed': 'FAILED'
            }
        )

        if response['Items']:
            return response['Items'][0]  # Return the first match
        return None

    except Exception as e:
        print(f"Error finding active document for '{base_filename}': {str(e)}")
        return None

def acquire_processing_lock(table, base_filename, ttl_seconds=30):
    """Atomically claim the right to process a base filename.

    Uses a conditional write on the same table (with a reserved document_id
    prefix that can never collide with a real UUID document_id) so that, when
    two Lambda invocations race to handle sibling pages of the same document,
    only one of them proceeds. The lock carries a short TTL (also enforced by
    the table's native TTL attribute) so a crashed invocation can never wedge
    a base filename permanently.
    """
    lock_id = f"__lock__{base_filename}"
    now = int(time.time())
    try:
        table.put_item(
            Item={
                'document_id': lock_id,
                'status': 'LOCK',
                'ttl': now + ttl_seconds,
                'created_at': datetime.utcnow().isoformat()
            },
            ConditionExpression='attribute_not_exists(document_id) OR #ttl < :now',
            ExpressionAttributeNames={'#ttl': 'ttl'},
            ExpressionAttributeValues={':now': now}
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        print(f"Error acquiring processing lock for '{base_filename}': {str(e)}")
        return False

def release_processing_lock(table, base_filename):
    """Release a lock acquired with acquire_processing_lock. Best-effort: the
    lock's own TTL is the real safety net if this fails."""
    try:
        table.delete_item(Key={'document_id': f"__lock__{base_filename}"})
    except Exception as e:
        print(f"Error releasing processing lock for '{base_filename}': {str(e)}")

def is_paginated_filename(filename):
    """Return True if the filename matches the multi-page pattern
    (basename_N.ext or basename-N.ext), i.e. it may have sibling pages."""
    basename = os.path.basename(filename)
    pattern = r'^(.+?)[_-](\d+)\.(.+)$'
    return re.match(pattern, basename) is not None

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