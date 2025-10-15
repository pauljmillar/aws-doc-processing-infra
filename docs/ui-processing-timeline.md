# UI Processing Timeline Guide

This guide explains how the UI app can interpret and display the processing steps for documents in the `docproc-documents` DynamoDB table.

## Overview

The document processing pipeline follows these main steps:
1. **File Upload** → S3 `incoming/` folder
2. **Ingest Lambda** → Creates document record, triggers Step Functions
3. **Step Functions Workflow** → Orchestrates the processing
4. **OCR Lambda** → Extracts text from images/PDFs
5. **Aggregator Lambda** → Combines text from multiple pages
6. **LLM Lambda** → Classifies and extracts structured data
7. **File Movement** → Moves files to `complete/` folder

## DynamoDB Fields for Timeline Construction

### Core Fields
- `document_id` (S) - Unique identifier
- `status` (S) - Current processing status
- `original_filename` (S) - Original uploaded filename
- `created_at` (S) - When document was first created
- `updated_at` (S) - Last update timestamp
- `last_error` (S) - Error message if processing failed

### Processing Fields
- `pages` (L) - List of original file paths
- `pages_received` (N) - Number of pages processed
- `textract_jobs` (M) - OCR job statuses per page
- `ocr_text_keys` (L) - S3 keys for extracted text files
- `combined_key` (S) - S3 key for combined text
- `result_key` (S) - S3 key for LLM results
- `moved_files` (L) - List of files moved to complete folder
- `document_type` (S) - Document type from LLM classification

### Workflow Fields
- `step_function_execution_arn` (S) - Step Functions execution ARN

## Status Values and Their Meanings

### Document Status (`status` field)
- `AWAITING_PAGES` - Document created, waiting for all pages
- `OCR_RUNNING` - OCR processing in progress
- `AGGREGATED` - Text aggregation completed
- `LLM_RUNNING` - LLM processing in progress
- `COMPLETE` - All processing completed successfully
- `FAILED` - Processing failed (check `last_error`)

### Textract Job Status (`textract_jobs` field)
- `SYNC_COMPLETE` - Synchronous OCR completed
- `ASYNC_STARTED` - Asynchronous OCR job started
- `ASYNC_COMPLETE` - Asynchronous OCR job completed
- `FAILED` - OCR job failed

## Building the Processing Timeline

### Step 1: Basic Timeline from DynamoDB

```javascript
function buildBasicTimeline(document) {
  const timeline = [];
  
  // Always start with file upload
  timeline.push({
    step: "File Upload",
    status: "completed",
    timestamp: document.created_at,
    details: `File: ${document.original_filename}`
  });
  
  // Check if Step Functions was triggered
  if (document.step_function_execution_arn) {
    timeline.push({
      step: "Workflow Triggered",
      status: "completed",
      timestamp: document.created_at,
      details: "Step Functions execution started"
    });
  }
  
  // Check OCR status
  if (document.textract_jobs) {
    const ocrStatus = Object.values(document.textract_jobs)[0];
    timeline.push({
      step: "OCR Processing",
      status: ocrStatus === "SYNC_COMPLETE" || ocrStatus === "ASYNC_COMPLETE" ? "completed" : 
              ocrStatus === "FAILED" ? "failed" : "in_progress",
      details: `OCR method: ${ocrStatus.includes("SYNC") ? "Synchronous" : "Asynchronous"}`
    });
  }
  
  // Check aggregation
  if (document.combined_key) {
    timeline.push({
      step: "Text Aggregation",
      status: "completed",
      details: "Text from all pages combined"
    });
  }
  
  // Check LLM processing
  if (document.result_key) {
    timeline.push({
      step: "LLM Processing",
      status: document.status === "COMPLETE" ? "completed" : 
              document.status === "FAILED" ? "failed" : "in_progress",
      details: document.document_type ? `Classified as: ${document.document_type}` : "Classification in progress"
    });
  }
  
  // Check file movement
  if (document.moved_files && document.moved_files.length > 0) {
    timeline.push({
      step: "File Movement",
      status: "completed",
      details: `Moved ${document.moved_files.length} file(s) to complete folder`
    });
  }
  
  return timeline;
}
```

### Step 2: Enhanced Timeline with Step Functions Details

```javascript
async function buildEnhancedTimeline(document) {
  const basicTimeline = buildBasicTimeline(document);
  
  if (document.step_function_execution_arn) {
    try {
      // Get Step Functions execution history
      const executionHistory = await getStepFunctionsHistory(document.step_function_execution_arn);
      
      // Enhance timeline with detailed step information
      const enhancedTimeline = basicTimeline.map(step => {
        const stepFunctionStep = executionHistory.find(sf => 
          step.step.toLowerCase().includes(sf.state.toLowerCase())
        );
        
        if (stepFunctionStep) {
          return {
            ...step,
            stepFunctionDetails: {
              state: stepFunctionStep.state,
              timestamp: stepFunctionStep.timestamp,
              duration: stepFunctionStep.duration
            }
          };
        }
        return step;
      });
      
      return enhancedTimeline;
    } catch (error) {
      console.error("Error getting Step Functions history:", error);
      return basicTimeline;
    }
  }
  
  return basicTimeline;
}
```

### Step 3: Error Handling and Special Cases

```javascript
function addErrorDetails(timeline, document) {
  if (document.status === "FAILED" && document.last_error) {
    // Find the step that failed
    const failedStep = timeline.find(step => step.status === "failed");
    if (failedStep) {
      failedStep.error = document.last_error;
      failedStep.errorType = categorizeError(document.last_error);
    }
  }
  
  return timeline;
}

function categorizeError(errorMessage) {
  if (errorMessage.includes("INVALID_IMAGE_TYPE")) {
    return "Invalid file type - not a valid image or PDF";
  } else if (errorMessage.includes("InvalidS3ObjectException")) {
    return "S3 access issue - file not found or permission denied";
  } else if (errorMessage.includes("OpenAI API call failed")) {
    return "LLM processing failed - API error";
  } else if (errorMessage.includes("No pages provided")) {
    return "No files to process";
  } else {
    return "Unknown error";
  }
}
```

## Example Timeline Outputs

### Successful Processing
```
Document ID: b0c121a8-9224-4637-9528-b066e492bca3
- File Upload: completed (2025-10-13T16:11:17)
- Workflow Triggered: completed (2025-10-13T16:11:17)
- OCR Processing: completed (Synchronous method)
- Text Aggregation: completed
- LLM Processing: completed (Classified as: promotion)
- File Movement: completed (Moved 1 file(s) to complete folder)
```

### Failed Processing
```
Document ID: test-fake-image-1760303213
- File Upload: completed (2025-10-13T10:45:00)
- Workflow Triggered: completed (2025-10-13T10:45:00)
- OCR Processing: failed (Invalid file type - not a valid image or PDF)
```

### ZIP File Processing
```
Document ID: zip-document-123
- File Upload: completed (2025-10-13T16:11:17)
- ZIP Extraction: completed (Extracted 3 files)
- Workflow Triggered: completed (2025-10-13T16:11:17)
- OCR Processing: completed (Asynchronous method)
- Text Aggregation: completed
- LLM Processing: completed (Classified as: invoice)
- File Movement: completed (Moved 3 file(s) to complete folder)
```

## API Endpoints for UI

### Get Document Timeline
```javascript
// GET /api/documents/{document_id}/timeline
async function getDocumentTimeline(documentId) {
  const document = await getDocumentFromDynamoDB(documentId);
  const timeline = await buildEnhancedTimeline(document);
  return addErrorDetails(timeline, document);
}
```

### Get Step Functions History
```javascript
// GET /api/step-functions/{executionArn}/history
async function getStepFunctionsHistory(executionArn) {
  const history = await stepFunctions.getExecutionHistory({
    executionArn: executionArn
  }).promise();
  
  return history.events
    .filter(event => ['TaskStateEntered', 'TaskSucceeded', 'TaskFailed'].includes(event.type))
    .map(event => ({
      type: event.type,
      state: event.stateEnteredEventDetails?.name,
      timestamp: event.timestamp,
      duration: event.taskSucceededEventDetails?.output ? 
        calculateDuration(event.taskStartedEventDetails?.timestamp, event.timestamp) : null
    }));
}
```

## Real-time Updates

For real-time updates, the UI can:

1. **Poll DynamoDB** - Check `updated_at` field for changes
2. **Use DynamoDB Streams** - Subscribe to real-time changes
3. **WebSocket Updates** - Push updates to connected clients

```javascript
// Polling example
setInterval(async () => {
  const document = await getDocumentFromDynamoDB(documentId);
  if (document.updated_at !== lastUpdateTime) {
    updateTimeline(await buildEnhancedTimeline(document));
    lastUpdateTime = document.updated_at;
  }
}, 5000); // Poll every 5 seconds
```

## Performance Considerations

1. **Cache Step Functions History** - Don't fetch on every request
2. **Batch DynamoDB Queries** - Get multiple documents at once
3. **Lazy Load Details** - Only fetch Step Functions history when needed
4. **Use DynamoDB GSI** - Query by status for dashboard views

This approach provides a comprehensive view of document processing that can be easily displayed in the UI with proper error handling and real-time updates.
