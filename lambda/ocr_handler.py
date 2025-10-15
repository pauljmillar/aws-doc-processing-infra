# Python Lambda function for OCR processing with Textract
import os
import json
import boto3
import time
from datetime import datetime

def lambda_handler(event, context):
    """
    Processes OCR using Amazon Textract for document pages.
    
    Expected event structure:
    {
        "document_id": "abcxyz",
        "pages": ["incoming/abcxyz_1.jpg", "incoming/abcxyz_2.jpg"],
        "status": "OCR_RUNNING"
    }
    """
    print("OCR Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        textract = boto3.client('textract')
        s3 = boto3.client('s3')
        dynamodb = boto3.resource('dynamodb')
        
        bucket_name = os.environ['BUCKET_NAME']
        table_name = os.environ['DOCUMENTS_TABLE']
        table = dynamodb.Table(table_name)
        
        document_id = event['document_id']
        pages = event.get('pages', [])
        
        if not pages:
            raise ValueError("No pages provided for OCR processing")
        
        # Get current document state
        response = table.get_item(Key={'document_id': document_id})
        if 'Item' not in response:
            raise ValueError(f"Document {document_id} not found in DynamoDB")
        
        doc_item = response['Item']
        textract_jobs = doc_item.get('textract_jobs', {})
        ocr_text_keys = doc_item.get('ocr_text_keys', [])
        
        # Determine processing strategy based on page count
        is_single_page = len(pages) == 1
        print(f"Processing {len(pages)} page(s) - {'SINGLE PAGE (synchronous)' if is_single_page else 'MULTI-PAGE (asynchronous)'}")
        
        # Process each page
        for page_key in pages:
            if page_key in textract_jobs:
                # Check if Textract job is complete (async only)
                if not is_single_page:
                    job_id = textract_jobs[page_key]
                    try:
                        response = textract.get_document_text_detection(JobId=job_id)
                        job_status = response['JobStatus']
                        
                        if job_status == 'SUCCEEDED':
                            # Extract text from Textract response
                            text_content = extract_text_from_textract_response(response)
                            
                            # Save text to S3
                            page_number = pages.index(page_key) + 1
                            text_key = f"staging/{document_id}/text_page_{page_number}.txt"
                            
                            s3.put_object(
                                Bucket=bucket_name,
                                Key=text_key,
                                Body=text_content.encode('utf-8'),
                                ContentType='text/plain'
                            )
                            
                            if text_key not in ocr_text_keys:
                                ocr_text_keys.append(text_key)
                            
                            print(f"Completed OCR for {page_key}, saved to {text_key}")
                            
                        elif job_status == 'FAILED':
                            error_message = response.get('StatusMessage', 'Unknown error')
                            raise Exception(f"Textract job failed for {page_key}: {error_message}")
                            
                        else:
                            print(f"Textract job {job_id} still in progress: {job_status}")
                            continue
                            
                    except textract.exceptions.InvalidJobIdException:
                        # Job doesn't exist, start new one
                        pass
                    except Exception as e:
                        print(f"Error checking Textract job: {str(e)}")
                        raise
            
            # Process page (synchronous for single, async for multi)
            if page_key not in textract_jobs:
                try:
                    if is_single_page:
                        # Use synchronous Textract for single pages (faster)
                        print(f"Processing single page synchronously: {page_key}")
                        response = textract.detect_document_text(
                            Document={
                                'S3Object': {
                                    'Bucket': bucket_name,
                                    'Name': page_key
                                }
                            }
                        )
                        
                        # Extract text immediately
                        text_content = extract_text_from_textract_response(response)
                        
                        # Save text to S3
                        text_key = f"staging/{document_id}/text_page_1.txt"
                        
                        s3.put_object(
                            Bucket=bucket_name,
                            Key=text_key,
                            Body=text_content.encode('utf-8'),
                            ContentType='text/plain'
                        )
                        
                        if text_key not in ocr_text_keys:
                            ocr_text_keys.append(text_key)
                        
                        # Mark as completed
                        textract_jobs[page_key] = 'SYNC_COMPLETE'
                        
                        print(f"Completed synchronous OCR for {page_key}, saved to {text_key}")
                        
                    else:
                        # Use asynchronous Textract for multi-page documents
                        print(f"Starting async Textract job for: {page_key}")
                        response = textract.start_document_text_detection(
                            DocumentLocation={
                                'S3Object': {
                                    'Bucket': bucket_name,
                                    'Name': page_key
                                }
                            }
                        )
                        
                        job_id = response['JobId']
                        textract_jobs[page_key] = job_id
                        
                        print(f"Started Textract job {job_id} for {page_key}")
                    
                except Exception as e:
                    print(f"Error processing {page_key}: {str(e)}")
                    raise
        
        # Update DynamoDB with current state
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET textract_jobs = :jobs, ocr_text_keys = :keys, updated_at = :timestamp',
            ExpressionAttributeValues={
                ':jobs': textract_jobs,
                ':keys': ocr_text_keys,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        # Check if all OCR jobs are complete
        all_complete = True
        for page_key in pages:
            if page_key not in textract_jobs:
                all_complete = False
                break
            
            job_id = textract_jobs[page_key]
            
            if is_single_page and job_id == 'SYNC_COMPLETE':
                # Single page synchronous processing is complete
                continue
            elif not is_single_page:
                # Multi-page async processing - check job status
                try:
                    response = textract.get_document_text_detection(JobId=job_id)
                    if response['JobStatus'] != 'SUCCEEDED':
                        all_complete = False
                        break
                except:
                    all_complete = False
                    break
            else:
                all_complete = False
                break
        
        if all_complete:
            # All OCR jobs complete, ready for aggregation
            table.update_item(
                Key={'document_id': document_id},
                UpdateExpression='SET #status = :status, updated_at = :timestamp',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'AGGREGATING',
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            
            # Check if PII processing should be enabled and send to queue
            pii_enabled = check_pii_processing_config(bucket_name)
            if pii_enabled:
                send_to_pii_queue(document_id, pages, ocr_text_keys, bucket_name)
            
            return {
                'statusCode': 200,
                'document_id': document_id,
                'pages': pages,
                'bucket_name': bucket_name,
                'status': 'ocr_complete',
                'ocr_text_keys': ocr_text_keys,
                'next_step': 'aggregate'
            }
        else:
            # Still processing, will be called again
            return {
                'statusCode': 200,
                'document_id': document_id,
                'pages': pages,
                'bucket_name': bucket_name,
                'status': 'ocr_in_progress',
                'textract_jobs': textract_jobs
            }
        
    except Exception as e:
        print(f"Error in OCR handler: {str(e)}")
        
        # Update DynamoDB with error
        try:
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
        except:
            pass
        
        raise

def extract_text_from_textract_response(response):
    """Extract text content from Textract response."""
    text_blocks = []
    
    # Process text detection results
    for block in response.get('Blocks', []):
        if block['BlockType'] == 'LINE':
            text_blocks.append(block['Text'])
    
    return '\n'.join(text_blocks)

def check_pii_processing_config(bucket_name):
    """Check if PII processing should be enabled for this bucket"""
    try:
        dynamodb = boto3.resource('dynamodb')
        config_table = dynamodb.Table(os.environ.get('CONFIG_TABLE', 'docproc-config'))
        
        response = config_table.get_item(
            Key={
                'config_key': 'pii_processing',
                'config_type': 'feature_flag'
            }
        )
        
        config = response.get('Item', {})
        if not config.get('enabled', False):
            return False
            
        conditions = config.get('conditions', {})
        allowed_buckets = conditions.get('s3_buckets', [])
        
        if allowed_buckets and bucket_name not in allowed_buckets:
            return False
            
        return True
        
    except Exception as e:
        print(f"Error checking PII config: {e}")
        return False  # Default to disabled on error

def send_to_pii_queue(document_id, pages, ocr_text_keys, bucket_name):
    """Send message to PII processing queue"""
    try:
        sqs = boto3.client('sqs')
        queue_url = f"https://sqs.{os.environ.get('REGION', 'us-west-2')}.amazonaws.com/{boto3.client('sts').get_caller_identity()['Account']}/docproc-queue-pii"
        
        message = {
            'document_id': document_id,
            'pages': pages,
            'ocr_text_keys': ocr_text_keys,
            'bucket_name': bucket_name
        }
        
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message)
        )
        
        print(f"Sent document {document_id} to PII processing queue")
        
    except Exception as e:
        print(f"Error sending to PII queue: {e}")
        # Don't fail the main process if PII queue fails
