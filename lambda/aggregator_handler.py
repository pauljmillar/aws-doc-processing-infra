# Python Lambda function for text aggregation
import os
import json
import boto3
from datetime import datetime

def lambda_handler(event, context):
    """
    Aggregates OCR text files into a single combined text file.
    
    Expected event structure:
    {
        "document_id": "abcxyz",
        "pages": ["incoming/abcxyz_1.jpg", "incoming/abcxyz_2.jpg"],
        "ocr_text_keys": ["staging/abcxyz/text_page_1.txt", "staging/abcxyz/text_page_2.txt"]
    }
    """
    print("Aggregator Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        dynamodb = boto3.resource('dynamodb')
        
        bucket_name = os.environ['BUCKET_NAME']
        table_name = os.environ['DOCUMENTS_TABLE']
        table = dynamodb.Table(table_name)
        
        document_id = event['document_id']
        ocr_text_keys = event.get('ocr_text_keys', [])
        
        if not ocr_text_keys:
            raise ValueError("No OCR text keys provided for aggregation")
        
        # Read and combine all OCR text files
        combined_text = ""
        for i, text_key in enumerate(ocr_text_keys):
            try:
                response = s3.get_object(Bucket=bucket_name, Key=text_key)
                text_content = response['Body'].read().decode('utf-8')
                combined_text += f"--- Page {i+1} ---\n{text_content}\n\n"
            except Exception as e:
                print(f"Error reading text file {text_key}: {str(e)}")
                raise
        
        # Save combined text to S3
        combined_key = f"staging/{document_id}/combined.txt"
        s3.put_object(
            Bucket=bucket_name,
            Key=combined_key,
            Body=combined_text.encode('utf-8'),
            ContentType='text/plain'
        )
        
        # Update DynamoDB with combined text key
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET combined_key = :key, updated_at = :timestamp',
            ExpressionAttributeValues={
                ':key': combined_key,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        print(f"Successfully aggregated text for document {document_id}")
        
        return {
            'statusCode': 200,
            'document_id': document_id,
            'combined_key': combined_key,
            'status': 'aggregated',
            'pages': event.get('pages', [])
        }
        
    except Exception as e:
        print(f"Error in aggregator: {str(e)}")
        
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
