#!/usr/bin/env python3
"""
Test script for multi-page document processing
"""

import boto3
import time
import json
from datetime import datetime

def test_multi_page_processing():
    """Test multi-page document processing with example files"""
    
    # Initialize AWS clients
    s3 = boto3.client('s3')
    dynamodb = boto3.resource('dynamodb')
    
    bucket_name = 'docproc-bucket'
    table_name = 'docproc-documents'
    table = dynamodb.Table(table_name)
    
    # Test document base name
    base_name = 'PATMULmobile1027414610TEST2'
    
    print("=== Multi-Page Document Processing Test ===")
    print(f"Base name: {base_name}")
    print(f"Bucket: {bucket_name}")
    print(f"Table: {table_name}")
    
    # Create test files
    test_files = [
        f"{base_name}-0.jpeg",
        f"{base_name}-1.jpeg"
    ]
    
    print(f"\nTest files: {test_files}")
    
    # Upload files to S3
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
        except Exception as e:
            print(f"✗ Failed to upload {filename}: {e}")
            return False
    
    # Wait for processing
    print("\n=== Waiting for Processing ===")
    print("Waiting 30 seconds for processing to complete...")
    time.sleep(30)
    
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

def check_s3_contents():
    """Check S3 bucket contents"""
    print("\n=== S3 Bucket Contents ===")
    
    s3 = boto3.client('s3')
    bucket_name = 'docproc-bucket'
    
    try:
        # List incoming folder
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix='incoming/'
        )
        
        incoming_files = response.get('Contents', [])
        print(f"Incoming folder: {len(incoming_files)} files")
        for obj in incoming_files:
            print(f"  - {obj['Key']}")
        
        # List complete folder
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix='complete/'
        )
        
        complete_files = response.get('Contents', [])
        print(f"Complete folder: {len(complete_files)} files")
        for obj in complete_files:
            print(f"  - {obj['Key']}")
        
        # List results folder
        response = s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix='results/'
        )
        
        results_files = response.get('Contents', [])
        print(f"Results folder: {len(results_files)} files")
        for obj in results_files:
            print(f"  - {obj['Key']}")
        
    except Exception as e:
        print(f"Error checking S3: {e}")

def main():
    """Run the test"""
    print("Multi-Page Document Processing Test")
    print("=" * 50)
    
    # Run the test
    success = test_multi_page_processing()
    
    # Check S3 contents
    check_s3_contents()
    
    if success:
        print("\n✅ Test completed successfully!")
    else:
        print("\n❌ Test failed!")
    
    return success

if __name__ == "__main__":
    main()
