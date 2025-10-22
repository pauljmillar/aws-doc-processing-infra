#!/usr/bin/env python3
"""
Test script for multi-page document processing - Version 2
"""

import boto3
import time
import json
from datetime import datetime

def test_multi_page_processing_v2():
    """Test multi-page document processing with example files"""
    
    # Initialize AWS clients
    s3 = boto3.client('s3')
    dynamodb = boto3.resource('dynamodb')
    
    bucket_name = 'docproc-bucket'
    table_name = 'docproc-documents'
    table = dynamodb.Table(table_name)
    
    # Test document base name
    base_name = 'PATMULmobile1027414610TEST3'
    
    print("=== Multi-Page Document Processing Test V2 ===")
    print(f"Base name: {base_name}")
    print(f"Bucket: {bucket_name}")
    print(f"Table: {table_name}")
    
    # Create test files
    test_files = [
        f"{base_name}-0.jpeg",
        f"{base_name}-1.jpeg"
    ]
    
    print(f"\nTest files: {test_files}")
    
    # Upload files to S3 with a small delay between uploads
    print("\n=== Uploading Files ===")
    for i, filename in enumerate(test_files):
        # Create a simple test image (1x1 pixel JPEG)
        test_content = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x01\x01\x11\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x08\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\xaa\xff\xd9'
        
        key = f"incoming/{filename}"
        
        try:
            s3.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=test_content,
                ContentType='image/jpeg'
            )
            print(f"✓ Uploaded: {filename}")
            
            # Add a small delay between uploads to simulate real-world scenario
            if i == 0:
                print("  Waiting 5 seconds before uploading second file...")
                time.sleep(5)
                
        except Exception as e:
            print(f"✗ Failed to upload {filename}: {e}")
            return False
    
    # Wait for processing
    print("\n=== Waiting for Processing ===")
    print("Waiting 60 seconds for processing to complete...")
    time.sleep(60)
    
    # Check DynamoDB for document records
    print("\n=== Checking Document Records ===")
    try:
        # Scan for documents with our base name
        response = table.scan(
            FilterExpression='begins_with(original_filename, :base_name)',
            ExpressionAttributeValues={':base_name': base_name}
        )
        
        documents = response.get('Items', [])
        print(f"Found {len(documents)} document(s)")
        
        for doc in documents:
            print(f"\nDocument ID: {doc['document_id']}")
            print(f"Status: {doc.get('status', 'unknown')}")
            print(f"Pages received: {doc.get('pages_received', 0)}")
            print(f"Pages: {doc.get('pages', [])}")
            print(f"Original filename: {doc.get('original_filename', 'unknown')}")
            
            if 'result_key' in doc:
                print(f"Result key: {doc['result_key']}")
            
            if 'moved_files' in doc:
                print(f"Moved files: {doc['moved_files']}")
        
        return len(documents) > 0
        
    except Exception as e:
        print(f"Error checking documents: {e}")
        return False

def check_staging_files(document_id):
    """Check staging files for a specific document"""
    print(f"\n=== Checking Staging Files for {document_id} ===")
    
    s3 = boto3.client('s3')
    bucket_name = 'docproc-bucket'
    
    try:
        # List staging files for this document
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix=f'staging/{document_id}/'
        )
        
        staging_files = response.get('Contents', [])
        print(f"Staging files: {len(staging_files)}")
        for obj in staging_files:
            print(f"  - {obj['Key']}")
            
            # If it's a text file, show its content
            if obj['Key'].endswith('.txt'):
                try:
                    content = s3.get_object(Bucket=bucket_name, Key=obj['Key'])
                    text = content['Body'].read().decode('utf-8')
                    print(f"    Content preview: {text[:100]}...")
                except Exception as e:
                    print(f"    Error reading content: {e}")
        
    except Exception as e:
        print(f"Error checking staging files: {e}")

def main():
    """Run the test"""
    print("Multi-Page Document Processing Test V2")
    print("=" * 50)
    
    # Run the test
    success = test_multi_page_processing_v2()
    
    if success:
        print("\n✅ Test completed successfully!")
        
        # Check staging files for the processed document
        # You can manually check the document ID from the output above
        print("\nTo check staging files, run:")
        print("aws s3 ls s3://docproc-bucket/staging/ --recursive")
    else:
        print("\n❌ Test failed!")
    
    return success

if __name__ == "__main__":
    main()
