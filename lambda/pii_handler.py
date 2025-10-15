# Python Lambda function for PII detection and image redaction
import os
import json
import boto3
import re
import io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

def lambda_handler(event, context):
    """
    Processes documents for PII detection and image redaction.
    
    Expected event structure (SQS format):
    {
        "Records": [
            {
                "body": "{\"document_id\": \"abcxyz\", \"pages\": [...], \"ocr_text_keys\": [...], \"bucket_name\": \"docproc-bucket\"}"
            }
        ]
    }
    """
    print("PII Event:", json.dumps(event, indent=2))
    
    try:
        # Initialize AWS clients
        s3 = boto3.client('s3')
        textract_client = boto3.client('textract')
        dynamodb = boto3.resource('dynamodb')
        
        bucket_name = os.environ['BUCKET_NAME']
        table_name = os.environ['DOCUMENTS_TABLE']
        table = dynamodb.Table(table_name)
        
        # Parse SQS message format
        if 'Records' in event:
            # SQS message format
            for record in event.get('Records', []):
                if record.get('eventSource') != 'aws:sqs':
                    continue
                
                try:
                    message_body = json.loads(record['body'])
                except json.JSONDecodeError:
                    print(f"Failed to parse SQS message body: {record['body']}")
                    continue
                
                document_id = message_body['document_id']
                pages = message_body.get('pages', [])
                ocr_text_keys = message_body.get('ocr_text_keys', [])
                bucket_name = message_body.get('bucket_name', bucket_name)
        else:
            # Direct message format (for testing)
            document_id = event['document_id']
            pages = event.get('pages', [])
            ocr_text_keys = event.get('ocr_text_keys', [])
            bucket_name = event.get('bucket_name', bucket_name)
        
        if not ocr_text_keys:
            raise ValueError("No OCR text keys provided for PII processing")
        
        print(f"Starting PII processing for document {document_id}")
        
        # Process each page for PII detection and redaction
        pii_results = {
            "status": "PII_PROCESSED",
            "detected_pii": False,
            "redacted_images": [],
            "pii_detections": [],
            "processing_summary": {}
        }
        
        for i, (page_key, ocr_text_key) in enumerate(zip(pages, ocr_text_keys)):
            print(f"Processing page {i+1}: {page_key}")
            
            # Get OCR text
            ocr_text = get_ocr_text(s3, bucket_name, ocr_text_key)
            
            # Get Textract response for bounding boxes
            textract_response = get_textract_response(textract_client, bucket_name, page_key)
            
            # Detect PII in text
            pii_detections = detect_pii_in_text(ocr_text)
            
            if pii_detections:
                pii_results["detected_pii"] = True
                pii_results["pii_detections"].extend(pii_detections)
                
                # Map PII text to bounding boxes
                bounding_boxes = map_pii_to_bounding_boxes(pii_detections, textract_response)
                
                # Redact image
                redacted_image_key = redact_image(s3, bucket_name, page_key, bounding_boxes, document_id, i+1)
                if redacted_image_key:
                    pii_results["redacted_images"].append(redacted_image_key)
                
                print(f"Redacted {len(bounding_boxes)} PII instances on page {i+1}")
            else:
                print(f"No PII detected on page {i+1}")
        
        # Update processing summary
        pii_results["processing_summary"] = {
            "total_pages": len(pages),
            "pages_with_pii": len(pii_results["redacted_images"]),
            "total_pii_instances": len(pii_results["pii_detections"]),
            "pii_types_detected": list(set([pii["type"] for pii in pii_results["pii_detections"]]))
        }
        
        # Save results to S3
        result_key = f"pii-results/{document_id}_pii_analysis.json"
        s3.put_object(
            Bucket=bucket_name,
            Key=result_key,
            Body=json.dumps(pii_results, indent=2),
            ContentType='application/json'
        )
        
        # Update DynamoDB with PII processing status
        table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET pii_processing_complete = :complete, pii_result_key = :result_key, pii_error = :error, updated_at = :timestamp',
            ExpressionAttributeValues={
                ':complete': True,
                ':result_key': result_key,
                ':error': None,
                ':timestamp': datetime.utcnow().isoformat()
            }
        )
        
        print(f"Successfully completed PII processing for document {document_id}")
        print(f"Detected PII: {pii_results['detected_pii']}")
        print(f"Total PII instances: {len(pii_results['pii_detections'])}")
        
        return {
            'statusCode': 200,
            'document_id': document_id,
            'pii_status': pii_results['status'],
            'detected_pii': pii_results['detected_pii'],
            'pii_count': len(pii_results['pii_detections'])
        }
        
    except Exception as e:
        print(f"Error in PII handler: {str(e)}")
        # Update DynamoDB with error status
        try:
            table.update_item(
                Key={'document_id': document_id},
                UpdateExpression='SET pii_processing_complete = :complete, pii_error = :error, updated_at = :timestamp',
                ExpressionAttributeValues={
                    ':complete': False,
                    ':error': str(e),
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
        except Exception as ddb_e:
            print(f"Error updating DynamoDB with PII failure: {ddb_e}")
        raise

def get_ocr_text(s3_client, bucket_name, ocr_text_key):
    """Get OCR text from S3"""
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=ocr_text_key)
        return response['Body'].read().decode('utf-8')
    except Exception as e:
        print(f"Error reading OCR text from {ocr_text_key}: {e}")
        return ""

def get_textract_response(textract_client, bucket_name, page_key):
    """Get Textract response for bounding box information"""
    try:
        # Use synchronous detection for bounding boxes
        response = textract_client.detect_document_text(
            Document={'S3Object': {'Bucket': bucket_name, 'Name': page_key}}
        )
        return response
    except Exception as e:
        print(f"Error getting Textract response for {page_key}: {e}")
        return None

def detect_pii_in_text(text):
    """Detect PII patterns in text with context awareness"""
    pii_detections = []
    
    # Define PII patterns with confidence levels
    patterns = {
        'ssn': {
            'pattern': r'\b\d{3}-\d{2}-\d{4}\b',
            'confidence': 'high'
        },
        'account_number': {
            'pattern': r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
            'confidence': 'high'
        },
        'email': {
            'pattern': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            'confidence': 'high'
        },
        'phone': {
            'pattern': r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
            'confidence': 'medium'
        },
        'address': {
            'pattern': r'\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Circle|Cir|Court|Ct)\b',
            'confidence': 'medium'
        },
        'zip_code': {
            'pattern': r'\b\d{5}(?:-\d{4})?\b',
            'confidence': 'low'
        }
    }
    
    # Context-aware name detection
    # Look for personal names in specific contexts (letters, forms, etc.)
    name_contexts = [
        r'(?:Dear|To|From|Mr\.|Mrs\.|Ms\.|Dr\.)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'(?:Name|Applicant|Customer|Client):\s*([A-Z][a-z]+\s+[A-Z][a-z]+)',
        r'(?:Signed|By):\s*([A-Z][a-z]+\s+[A-Z][a-z]+)'
    ]
    
    # Check each PII pattern
    for pii_type, config in patterns.items():
        matches = re.finditer(config['pattern'], text, re.IGNORECASE)
        for match in matches:
            pii_detections.append({
                'type': pii_type,
                'text': match.group(),
                'start_pos': match.start(),
                'end_pos': match.end(),
                'confidence': config['confidence']
            })
    
    # Check for personal names in specific contexts
    for context_pattern in name_contexts:
        matches = re.finditer(context_pattern, text, re.IGNORECASE)
        for match in matches:
            name = match.group(1)
            # Additional validation: ensure it looks like a real name
            if is_likely_personal_name(name, text):
                pii_detections.append({
                    'type': 'personal_name',
                    'text': name,
                    'start_pos': match.start(1),
                    'end_pos': match.end(1),
                    'confidence': 'medium'
                })
    
    return pii_detections

def is_likely_personal_name(name, full_text):
    """Determine if a name is likely personal vs business"""
    # Simple heuristics to distinguish personal from business names
    business_indicators = [
        'LLC', 'Inc', 'Corp', 'Company', 'Associates', 'Group', 'Partners',
        'Chevrolet', 'Ford', 'Toyota', 'Honda', 'BMW', 'Mercedes', 'Audi',
        'Bank', 'Credit', 'Union', 'Insurance', 'Agency', 'Services'
    ]
    
    # If the name contains business indicators, it's likely not personal
    for indicator in business_indicators:
        if indicator.lower() in name.lower():
            return False
    
    # If the name appears in a business context, it's likely not personal
    business_contexts = [
        'from', 'at', 'company', 'business', 'dealer', 'dealership'
    ]
    
    # Get context around the name
    name_pos = full_text.lower().find(name.lower())
    if name_pos != -1:
        context_start = max(0, name_pos - 50)
        context_end = min(len(full_text), name_pos + len(name) + 50)
        context = full_text[context_start:context_end].lower()
        
        for business_context in business_contexts:
            if business_context in context:
                return False
    
    return True

def map_pii_to_bounding_boxes(pii_detections, textract_response):
    """Map PII text positions to bounding box coordinates"""
    if not textract_response:
        return []
    
    bounding_boxes = []
    
    # Create a mapping of text to bounding boxes from Textract
    text_to_boxes = {}
    for block in textract_response.get('Blocks', []):
        if block['BlockType'] == 'LINE':
            text = block['Text']
            bbox = block['Geometry']['BoundingBox']
            text_to_boxes[text] = bbox
    
    # Map PII detections to bounding boxes
    for pii in pii_detections:
        pii_text = pii['text']
        
        # Try exact match first
        if pii_text in text_to_boxes:
            bounding_boxes.append({
                'type': pii['type'],
                'text': pii_text,
                'bounding_box': text_to_boxes[pii_text],
                'confidence': pii['confidence']
            })
        else:
            # Try partial match (PII might be part of a larger text block)
            for text_block, bbox in text_to_boxes.items():
                if pii_text.lower() in text_block.lower():
                    bounding_boxes.append({
                        'type': pii['type'],
                        'text': pii_text,
                        'bounding_box': bbox,
                        'confidence': pii['confidence']
                    })
                    break
    
    return bounding_boxes

def redact_image(s3_client, bucket_name, page_key, bounding_boxes, document_id, page_num):
    """Redact PII from image using bounding boxes"""
    try:
        # Download image from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=page_key)
        image_data = response['Body'].read()
        
        # Open image with PIL
        image = Image.open(io.BytesIO(image_data))
        draw = ImageDraw.Draw(image)
        
        # Get image dimensions
        img_width, img_height = image.size
        
        # Redact each bounding box
        for bbox_info in bounding_boxes:
            bbox = bbox_info['bounding_box']
            
            # Convert normalized coordinates to pixel coordinates
            left = int(bbox['Left'] * img_width)
            top = int(bbox['Top'] * img_height)
            right = int((bbox['Left'] + bbox['Width']) * img_width)
            bottom = int((bbox['Top'] + bbox['Height']) * img_height)
            
            # Add some padding around the text for better redaction
            padding = 8
            left = max(0, left - padding)
            top = max(0, top - padding)
            right = min(img_width, right + padding)
            bottom = min(img_height, bottom + padding)
            
            # Draw white rectangle to redact
            draw.rectangle([left, top, right, bottom], fill='white', outline='white')
            
            print(f"Redacted {bbox_info['type']}: '{bbox_info['text']}' at ({left},{top},{right},{bottom})")
        
        # Save redacted image
        redacted_key = f"results/{document_id}_page_{page_num}_redacted.jpg"
        
        # Convert to bytes
        img_buffer = io.BytesIO()
        image.save(img_buffer, format='JPEG', quality=95)
        img_buffer.seek(0)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=redacted_key,
            Body=img_buffer.getvalue(),
            ContentType='image/jpeg'
        )
        
        return redacted_key
        
    except Exception as e:
        print(f"Error redacting image {page_key}: {e}")
        return None