// Simple Timeline Generator for UI
// Copy/paste this into your UI app

const AWS = require('aws-sdk');

// Configure AWS
AWS.config.update({
  region: 'us-west-2'
});

const dynamodb = new AWS.DynamoDB.DocumentClient();
const stepFunctions = new AWS.StepFunctions();

/**
 * Get simple timeline for a document
 * @param {string} documentId - The document ID
 * @returns {Promise<string>} Formatted timeline string
 */
async function getDocumentTimeline(documentId) {
  try {
    // Get document from DynamoDB
    const doc = await dynamodb.get({
      TableName: 'docproc-documents',
      Key: { document_id: documentId }
    }).promise();
    
    if (!doc.Item) {
      return `Document ID: ${documentId}\n- Document not found`;
    }
    
    const document = doc.Item;
    const timeline = [];
    
    // File Upload
    timeline.push(`- File Upload: completed (${formatDate(document.created_at)})`);
    
    // ZIP Extraction (if applicable)
    if (document.zip_extraction) {
      timeline.push(`- ZIP Extraction: completed (Extracted ${document.zip_extraction.extracted_count || 0} files)`);
    }
    
    // Workflow Triggered
    if (document.step_function_execution_arn) {
      timeline.push(`- Workflow Triggered: completed (${formatDate(document.created_at)})`);
    }
    
    // OCR Processing
    if (document.textract_jobs) {
      const ocrStatuses = Object.values(document.textract_jobs);
      const hasFailures = ocrStatuses.some(status => status === 'FAILED');
      const allComplete = ocrStatuses.every(status => 
        status === 'SYNC_COMPLETE' || status === 'ASYNC_COMPLETE'
      );
      
      const status = hasFailures ? 'failed' : (allComplete ? 'completed' : 'in_progress');
      const method = getOCRMethod(document.textract_jobs);
      timeline.push(`- OCR Processing: ${status} (${method})`);
    }
    
    // PII Processing
    if (document.pii_processing_complete !== undefined) {
      const piiStatus = document.pii_processing_complete ? 'completed' : 'in_progress';
      const piiDetails = document.pii_error ? `Error: ${categorizeError(document.pii_error)}` : 'PII analysis completed';
      timeline.push(`- PII Processing: ${piiStatus} (${piiDetails})`);
    }
    
    // Text Aggregation
    if (document.combined_key) {
      timeline.push(`- Text Aggregation: completed`);
    }
    
    // LLM Processing
    if (document.result_key || document.status === 'LLM_RUNNING') {
      const llmStatus = document.status === 'COMPLETE' ? 'completed' : 
                       document.status === 'FAILED' ? 'failed' : 'in_progress';
      
      const details = document.document_type ? 
        `Classified as: ${document.document_type}` : 
        'Classification in progress';
      
      timeline.push(`- LLM Processing: ${llmStatus} (${details})`);
    }
    
    // File Movement
    if (document.moved_files && document.moved_files.length > 0) {
      timeline.push(`- File Movement: completed (Moved ${document.moved_files.length} file(s) to complete folder)`);
    }
    
    // Add error if failed
    if (document.status === 'FAILED' && document.last_error) {
      timeline.push(`- Error: ${categorizeError(document.last_error)}`);
    }
    
    // Format final output
    return `Document ID: ${documentId}\n${timeline.join('\n')}`;
    
  } catch (error) {
    return `Document ID: ${documentId}\n- Error: ${error.message}`;
  }
}

/**
 * Get OCR method description
 */
function getOCRMethod(textractJobs) {
  const jobs = Object.entries(textractJobs);
  const syncJobs = jobs.filter(([_, status]) => status === 'SYNC_COMPLETE').length;
  const asyncJobs = jobs.filter(([_, status]) => 
    status === 'ASYNC_STARTED' || status === 'ASYNC_COMPLETE'
  ).length;
  
  if (syncJobs > 0 && asyncJobs > 0) {
    return `Mixed processing: ${syncJobs} synchronous, ${asyncJobs} asynchronous`;
  } else if (syncJobs > 0) {
    return `Synchronous method`;
  } else if (asyncJobs > 0) {
    return `Asynchronous method`;
  } else {
    return 'OCR processing';
  }
}

/**
 * Categorize error messages
 */
function categorizeError(errorMessage) {
  if (errorMessage.includes('INVALID_IMAGE_TYPE')) {
    return 'Invalid file type - not a valid image or PDF';
  } else if (errorMessage.includes('InvalidS3ObjectException')) {
    return 'S3 access issue - file not found or permission denied';
  } else if (errorMessage.includes('OpenAI API call failed')) {
    return 'LLM processing failed - API error';
  } else if (errorMessage.includes('No pages provided')) {
    return 'No files to process';
  } else if (errorMessage.includes('timeout')) {
    return 'Processing timeout';
  } else {
    return errorMessage;
  }
}

/**
 * Format date for display
 */
function formatDate(dateString) {
  if (!dateString) return 'Unknown date';
  return new Date(dateString).toISOString().replace('T', ' ').substring(0, 19);
}

// Example usage:
// getDocumentTimeline('b0c121a8-9224-4637-9528-b066e492bca3')
//   .then(timeline => console.log(timeline))
//   .catch(error => console.error('Error:', error));

module.exports = {
  getDocumentTimeline
};
