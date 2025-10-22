#!/usr/bin/env python3
"""
Test script to directly invoke the Lambda function
"""
import boto3
import json

def test_lambda_direct():
    """Test the Lambda function directly"""
    lambda_client = boto3.client('lambda')
    
    # Create test payload
    payload = {
        "Records": [
            {
                "s3": {
                    "bucket": {
                        "name": "docproc-bucket"
                    },
                    "object": {
                        "key": "incoming/SMARTBATCHTEST-0.jpg"
                    }
                }
            }
        ]
    }
    
    print("Testing Lambda function directly...")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = lambda_client.invoke(
            FunctionName='docproc-ingest',
            Payload=json.dumps(payload)
        )
        
        print(f"Response Status Code: {response['StatusCode']}")
        
        # Read the response
        response_payload = json.loads(response['Payload'].read())
        print(f"Response Payload: {json.dumps(response_payload, indent=2)}")
        
    except Exception as e:
        print(f"Error invoking Lambda: {str(e)}")

if __name__ == "__main__":
    test_lambda_direct()
