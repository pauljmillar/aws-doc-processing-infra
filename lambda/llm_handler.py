# Python Lambda function for LLM processing with OpenAI
import os
import json
import boto3
import urllib.request
import urllib.parse
from datetime import datetime

def get_openai_key():
    secret_name = os.environ["OPENAI_SECRET_NAME"]
    region = os.environ["REGION"]
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId=secret_name)
    return json.loads(resp["SecretString"])["OPENAI_API_KEY"]

def lambda_handler(event, context):
    """
    Processes documents through LLM with schema-based classification and extraction.
    
    Expected event structure:
    {
        "document_id": "abcxyz",
        "combined_key": "staging/abcxyz/combined.txt",
        "pages": ["incoming/abcxyz_1.jpg", "incoming/abcxyz_2.jpg"],
        "status": "LLM_RUNNING"
    }
    """
    print("LLM Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        dynamodb = boto3.resource('dynamodb')
        
        bucket_name = os.environ['BUCKET_NAME']
        table_name = os.environ['DOCUMENTS_TABLE']
        table = dynamodb.Table(table_name)
        
        document_id = event['document_id']
        combined_key = event.get('combined_key')
        pages = event.get('pages', [])
        
        if not combined_key:
            raise ValueError("No combined text key provided for LLM processing")
        
        # Read combined text from S3
        try:
            response = s3.get_object(Bucket=bucket_name, Key=combined_key)
            document_text = response['Body'].read().decode('utf-8')
        except Exception as e:
            raise Exception(f"Failed to read combined text from {combined_key}: {str(e)}")
        
        # Load schema files from S3
        schemas = load_schemas_from_s3(s3, bucket_name)
        
        # Get OpenAI API key
        openai_key = get_openai_key()
        
        # Process through LLM pipeline
        print(f"Starting LLM processing for document {document_id}")
        print(f"Document text length: {len(document_text)}")
        print(f"Available schemas: {list(schemas.keys())}")
        results = process_document_with_llm(document_text, schemas, openai_key)
        print(f"LLM processing completed. Results: {json.dumps(results, indent=2)}")
        
        # Extract document_type from classification results
        classification_result = results.get('document_analysis', {}).get('classification', {})
        if classification_result.get('success', False):
            document_type = classification_result.get('data', {}).get('document_type', 'unknown')
            print(f"Document type determined: {document_type}")
        else:
            # API call failed, raise an error
            error_msg = classification_result.get('error', 'Unknown error')
            raise Exception(f"OpenAI API call failed: {error_msg}")
        
        # Save results to S3
        result_key = f"results/{document_id}_response.json"
        s3.put_object(
            Bucket=bucket_name,
            Key=result_key,
            Body=json.dumps(results, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        
        # Move original files to complete folder
        moved_files = move_files_to_complete(s3, bucket_name, document_id, pages)
        
        # Update DynamoDB with completion, document_type, and full LLM results
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET #status = :status, result_key = :result, moved_files = :files, document_type = :doc_type, llm_results = :llm_results, updated_at = :timestamp',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'COMPLETE',
                ':result': result_key,
                ':files': moved_files,
                ':doc_type': document_type,
                ':llm_results': json.dumps(results),  # Store full JSON results
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        print(f"Successfully processed document {document_id}")
        
        return {
            'statusCode': 200,
            'document_id': document_id,
            'result_key': result_key,
            'status': 'complete'
        }
        
    except Exception as e:
        print(f"Error in LLM handler: {str(e)}")
        
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

def load_schemas_from_s3(s3_client, bucket_name):
    """Load schema files from S3 system-schemas folder."""
    schemas = {}
    
    try:
        # List objects in system-schemas folder
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix='system-schemas/'
        )
        
        for obj in response.get('Contents', []):
            key = obj['Key']
            if key.endswith('.json'):
                try:
                    # Load schema file
                    schema_response = s3_client.get_object(Bucket=bucket_name, Key=key)
                    schema_content = schema_response['Body'].read().decode('utf-8')
                    schema_name = key.split('/')[-1].replace('.json', '')
                    schemas[schema_name] = json.loads(schema_content)
                    print(f"Loaded schema: {schema_name}")
                except Exception as e:
                    print(f"Error loading schema {key}: {str(e)}")
    
    except Exception as e:
        print(f"Error listing schema files: {str(e)}")
    
    # Default schema if none found
    if not schemas:
        schemas['default'] = {
            "classification": {
                "description": "Classify the document type and extract key information",
                "fields": ["document_type", "confidence", "key_entities"]
            }
        }
    
    return schemas

def process_document_with_llm(document_text, schemas, openai_key):
    """Process document through LLM with multiple schema passes."""
    print(f"process_document_with_llm called with schemas: {list(schemas.keys())}")
    results = {
        'document_analysis': {},
        'processing_timestamp': datetime.utcnow().isoformat(),
        'schema_passes': []
    }
    
    # First pass: Classification
    if 'classification' in schemas:
        print("Starting classification pass...")
        classification_result = call_openai_api(
            document_text, 
            schemas['classification'], 
            openai_key,
            "classification"
        )
        print(f"Classification result: {json.dumps(classification_result, indent=2)}")
        results['document_analysis']['classification'] = classification_result
        results['schema_passes'].append('classification')
        
        # Extract document type from classification result
        if classification_result.get('success', False):
            doc_type = classification_result.get('data', {}).get('document_type', 'unknown')
            print(f"Document classified as: {doc_type}")
            
            # Second pass: Specific schema extraction based on classification
            schema_mapping = {
                'promotion': 'banking',  # Promotions use banking schema
                'invoice': 'invoice',
                'banking': 'banking',
                'credit_card': 'credit_cards',
                'insurance': 'insurance',
                'receipt': 'invoice',  # Receipts can use invoice schema
                'contract': 'invoice',  # Contracts can use invoice schema
                'letter': 'invoice'     # Letters can use invoice schema
            }
            
            # Map document type to schema name
            schema_name = schema_mapping.get(doc_type, doc_type)
            
            # Look for specific schema for this document type
            if schema_name in schemas:
                print(f"Starting specific extraction pass for {schema_name}...")
                specific_result = call_openai_api(
                    document_text,
                    schemas[schema_name],
                    openai_key,
                    f"specific_extraction_{schema_name}"
                )
                print(f"Specific extraction result: {json.dumps(specific_result, indent=2)}")
                results['document_analysis']['specific_extraction'] = specific_result
                results['schema_passes'].append(f'specific_extraction_{schema_name}')
            else:
                print(f"No specific schema found for {schema_name}, available schemas: {list(schemas.keys())}")
        else:
            print("Classification failed, skipping specific extraction")
    
    return results

def call_openai_api(document_text, schema, openai_key, pass_name):
    """Call OpenAI API with the given schema."""
    print(f"call_openai_api called for {pass_name} with key length: {len(openai_key)}")
    try:
        # Prepare the prompt based on schema
        if pass_name == "classification":
            prompt = f"""
Please analyze the following document text and extract information according to this schema:

Schema: {json.dumps(schema, indent=2)}

IMPORTANT CLASSIFICATION GUIDELINES:
- For document_type: Use "promotion" for ads, marketing materials, offers, deals, or promotional content (email, social media, direct mail, etc.)
- For promotions: Always set the industry field and primary_company field
- For co-branded offers (e.g., "American Airlines Mastercard"): Set primary_company as the main brand (American Airlines) and secondary_company as the partner (Mastercard)
- If document type is unclear, use "other"
- Industry must be one of the exact values listed in the enum
- For Credit Card industry, category should be one of the specific credit card categories listed

Document Text:
{document_text}

Please respond with a JSON object that follows the schema structure exactly.
"""
        else:
            prompt = f"""
Please analyze the following document text and extract information according to this schema:

Schema: {json.dumps(schema, indent=2)}

Document Text:
{document_text}

Please respond with a JSON object that follows the schema structure.
"""
        
        headers = {
            'Authorization': f'Bearer {openai_key}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'model': 'gpt-3.5-turbo',
            'messages': [
                {'role': 'system', 'content': 'You are a document analysis assistant. Respond only with valid JSON.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 2000
        }
        
        # Convert data to JSON string
        json_data = json.dumps(data).encode('utf-8')
        
        # Create request
        req = urllib.request.Request(
            'https://api.openai.com/v1/chat/completions',
            data=json_data,
            headers=headers,
            method='POST'
        )
        
        # Make request
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status == 200:
                    result = json.loads(response.read().decode('utf-8'))
                    content = result['choices'][0]['message']['content']
                    
                    # Try to parse as JSON
                    try:
                        parsed_content = json.loads(content)
                        return {
                            'success': True,
                            'data': parsed_content,
                            'raw_response': content
                        }
                    except json.JSONDecodeError:
                        return {
                            'success': True,
                            'data': {'raw_text': content},
                            'raw_response': content
                        }
                else:
                    return {
                        'success': False,
                        'error': f'OpenAI API error: {response.status} - {response.read().decode("utf-8")}'
                    }
        except urllib.error.HTTPError as e:
            return {
                'success': False,
                'error': f'HTTP error: {e.code} - {e.read().decode("utf-8")}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Request error: {str(e)}'
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': f'Exception calling OpenAI API: {str(e)}'
        }

def move_files_to_complete(s3_client, bucket_name, document_id, pages):
    """Move original files to complete folder."""
    moved_files = []
    
    for page_key in pages:
        try:
            # Copy to complete folder
            new_key = page_key.replace('incoming/', f'complete/{document_id}/')
            
            # Ensure the destination folder exists
            copy_source = {'Bucket': bucket_name, 'Key': page_key}
            s3_client.copy_object(
                CopySource=copy_source,
                Bucket=bucket_name,
                Key=new_key
            )
            
            # Delete original
            s3_client.delete_object(Bucket=bucket_name, Key=page_key)
            
            moved_files.append(new_key)
            print(f"Moved {page_key} to {new_key}")
            
        except Exception as e:
            print(f"Error moving file {page_key}: {str(e)}")
    
    return moved_files
