# Python Lambda function for ZIP file extraction
import os
import json
import boto3
import zipfile
import tempfile
import uuid
from datetime import datetime
from urllib.parse import unquote_plus

def lambda_handler(event, context):
    """
    Extracts ZIP files and uploads individual files to incoming folder.
    
    Expected S3 event structure:
    {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "bucket-name"},
                    "object": {"key": "incoming/document.zip"}
                }
            }
        ]
    }
    """
    print("ZIP Extractor Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        
        bucket_name = os.environ['BUCKET_NAME']
        extracted_files = []
        
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
                
            # Check if this is a test event
            if s3_event.get('Event') == 's3:TestEvent':
                print("Received S3 test event, skipping")
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
                
                # Check if file is a ZIP file
                filename = key.split('/')[-1]
                if not filename.lower().endswith('.zip'):
                    print(f"Skipping non-ZIP file: {filename}")
                    continue
                
                print(f"Processing ZIP file: {filename}")
                
                # Extract ZIP file
                extracted_count = extract_zip_file(s3, bucket_name, key, filename)
                extracted_files.append({
                    'zip_file': filename,
                    'extracted_count': extracted_count
                })
                
                # Move original ZIP to archive folder
                archive_key = f"archive/{filename}"
                s3.copy_object(
                    CopySource={'Bucket': bucket_name, 'Key': key},
                    Bucket=bucket_name,
                    Key=archive_key
                )
                s3.delete_object(Bucket=bucket_name, Key=key)
                
                print(f"Moved ZIP file to archive: {archive_key}")
        
        return {
            'statusCode': 200,
            'extracted_files': extracted_files,
            'status': 'success'
        }
        
    except Exception as e:
        print(f"Error in ZIP extractor: {str(e)}")
        raise

def extract_zip_file(s3, bucket_name, zip_key, zip_filename):
    """
    Extract ZIP file and upload individual files to incoming folder.
    """
    extracted_count = 0
    
    # Create temporary directory for extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        # Download ZIP file to temporary location
        zip_path = os.path.join(temp_dir, zip_filename)
        s3.download_file(bucket_name, zip_key, zip_path)
        
        # Extract ZIP file
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                # Skip directories
                if file_info.is_dir():
                    continue
                
                # Extract file to temporary location
                extracted_path = zip_ref.extract(file_info, temp_dir)
                
                # Generate unique filename to avoid conflicts
                original_filename = file_info.filename
                file_extension = os.path.splitext(original_filename)[1]
                unique_filename = f"{uuid.uuid4()}{file_extension}"
                
                # Upload extracted file to incoming folder
                incoming_key = f"incoming/{unique_filename}"
                
                try:
                    s3.upload_file(
                        extracted_path,
                        bucket_name,
                        incoming_key,
                        ExtraArgs={
                            'ContentType': get_content_type(file_extension),
                            'Metadata': {
                                'original_filename': original_filename,
                                'extracted_from': zip_filename,
                                'extraction_timestamp': datetime.utcnow().isoformat()
                            }
                        }
                    )
                    
                    print(f"Extracted: {original_filename} -> {unique_filename}")
                    extracted_count += 1
                    
                except Exception as e:
                    print(f"Error uploading extracted file {original_filename}: {str(e)}")
                    continue
    
    print(f"Extracted {extracted_count} files from {zip_filename}")
    return extracted_count

def get_content_type(file_extension):
    """
    Get appropriate content type for file extension.
    """
    content_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.pdf': 'application/pdf',
        '.txt': 'text/plain',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    }
    
    return content_types.get(file_extension.lower(), 'application/octet-stream')
