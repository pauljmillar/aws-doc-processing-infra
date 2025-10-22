#!/usr/bin/env python3
"""
Test script for the fixed Smart Batching functionality
"""
import boto3
import time
from datetime import datetime

def create_test_image(filename, content="Test image content"):
    """Create a simple test image file"""
    with open(filename, 'w') as f:
        f.write(content)

def upload_to_s3(bucket_name, local_file, s3_key):
    """Upload file to S3"""
    s3 = boto3.client('s3')
    s3.upload_file(local_file, bucket_name, s3_key)
    print(f"Uploaded {local_file} to s3://{bucket_name}/{s3_key}")

def check_dynamodb_status(table_name, document_id):
    """Check document status in DynamoDB"""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    response = table.get_item(Key={'document_id': document_id})
    if 'Item' in response:
        item = response['Item']
        return {
            'status': item.get('status', 'UNKNOWN'),
            'pages': item.get('pages', []),
            'pages_received': item.get('pages_received', 0),
            'window_expires_at': item.get('window_expires_at', 'N/A')
        }
    return None

def main():
    bucket_name = "docproc-bucket"
    table_name = "docproc-documents"
    
    # Test document base name
    base_name = "SMARTBATCHFIXED"
    document_id = f"{base_name}-{int(datetime.utcnow().timestamp())}"
    
    print(f"Testing Fixed Smart Batching with document ID: {document_id}")
    print("=" * 60)
    
    # Create test files
    test_files = [
        f"{base_name}-0.jpg",
        f"{base_name}-1.jpg"
    ]
    
    # Create local test files
    for filename in test_files:
        create_test_image(filename, f"Test content for {filename}")
    
    print("1. Uploading first file (should start processing immediately)...")
    upload_to_s3(bucket_name, test_files[0], f"incoming/{test_files[0]}")
    
    # Wait a moment for processing
    time.sleep(3)
    
    # Check status
    status = check_dynamodb_status(table_name, document_id)
    if status:
        print(f"   Status: {status['status']}")
        print(f"   Pages: {status['pages_received']}")
        print(f"   Window expires: {status['window_expires_at']}")
    else:
        print("   Document not found in DynamoDB")
    
    print("\n2. Uploading second file (should trigger smart batching)...")
    upload_to_s3(bucket_name, test_files[1], f"incoming/{test_files[1]}")
    
    # Wait a moment
    time.sleep(3)
    
    # Check status again
    status = check_dynamodb_status(table_name, document_id)
    if status:
        print(f"   Status: {status['status']}")
        print(f"   Pages: {status['pages_received']}")
        print(f"   Window expires: {status['window_expires_at']}")
    
    print("\n3. Waiting for window to expire and processing to start...")
    print("   (Window should expire in ~3 seconds after last upload)")
    
    # Wait for window to expire
    time.sleep(5)
    
    # Check final status
    status = check_dynamodb_status(table_name, document_id)
    if status:
        print(f"   Final Status: {status['status']}")
        print(f"   Final Pages: {status['pages_received']}")
        print(f"   All pages: {status['pages']}")
    
    # Cleanup
    print("\n4. Cleaning up test files...")
    for filename in test_files:
        try:
            import os
            os.remove(filename)
            print(f"   Removed {filename}")
        except:
            pass
    
    print("\nTest completed!")

if __name__ == "__main__":
    main()
