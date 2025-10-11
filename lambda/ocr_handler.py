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
        
        # Process each page
        for page_key in pages:
            if page_key in textract_jobs:
                # Check if Textract job is complete
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
            
            # Start new Textract job if not already started
            if page_key not in textract_jobs:
                try:
                    # Start asynchronous text detection
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
                    print(f"Error starting Textract job for {page_key}: {str(e)}")
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
            
            try:
                response = textract.get_document_text_detection(JobId=textract_jobs[page_key])
                if response['JobStatus'] != 'SUCCEEDED':
                    all_complete = False
                    break
            except:
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
