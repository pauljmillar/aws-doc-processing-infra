// Example API endpoints for the UI app
// This can be implemented in Node.js/Express, Python/Flask, or any backend framework

const AWS = require('aws-sdk');
const express = require('express');
const app = express();

// Configure AWS
AWS.config.update({
  region: process.env.AWS_REGION || 'us-west-2'
});

const dynamodb = new AWS.DynamoDB.DocumentClient();
const stepFunctions = new AWS.StepFunctions();

// Middleware
app.use(express.json());

/**
 * GET /api/documents/:documentId/timeline
 * Get processing timeline for a specific document
 */
app.get('/api/documents/:documentId/timeline', async (req, res) => {
  try {
    const { documentId } = req.params;
    
    // Get document from DynamoDB
    const document = await getDocument(documentId);
    
    if (!document) {
      return res.status(404).json({ error: 'Document not found' });
    }

    // Build timeline
    const timeline = await buildDocumentTimeline(document);
    
    res.json({
      documentId,
      timeline,
      summary: buildTimelineSummary(timeline, document)
    });
    
  } catch (error) {
    console.error('Error getting document timeline:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

/**
 * GET /api/documents/:documentId/status
 * Get current status of a document
 */
app.get('/api/documents/:documentId/status', async (req, res) => {
  try {
    const { documentId } = req.params;
    
    const document = await getDocument(documentId);
    
    if (!document) {
      return res.status(404).json({ error: 'Document not found' });
    }

    res.json({
      documentId,
      status: document.status,
      originalFilename: document.original_filename,
      documentType: document.document_type,
      createdAt: document.created_at,
      updatedAt: document.updated_at,
      lastError: document.last_error,
      progress: calculateProgress(document)
    });
    
  } catch (error) {
    console.error('Error getting document status:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

/**
 * GET /api/documents
 * Get list of documents with pagination
 */
app.get('/api/documents', async (req, res) => {
  try {
    const { 
      status, 
      limit = 50, 
      lastKey,
      sortBy = 'created_at',
      sortOrder = 'desc'
    } = req.query;

    const params = {
      TableName: 'docproc-documents',
      Limit: parseInt(limit)
    };

    // Add filter if status specified
    if (status) {
      params.FilterExpression = '#status = :status';
      params.ExpressionAttributeNames = { '#status': 'status' };
      params.ExpressionAttributeValues = { ':status': status };
    }

    // Add pagination
    if (lastKey) {
      params.ExclusiveStartKey = JSON.parse(decodeURIComponent(lastKey));
    }

    const result = await dynamodb.scan(params).promise();
    
    // Sort results
    const sortedItems = result.Items.sort((a, b) => {
      const aValue = a[sortBy] || '';
      const bValue = b[sortBy] || '';
      
      if (sortOrder === 'desc') {
        return bValue.localeCompare(aValue);
      } else {
        return aValue.localeCompare(bValue);
      }
    });

    res.json({
      documents: sortedItems.map(item => ({
        documentId: item.document_id,
        status: item.status,
        originalFilename: item.original_filename,
        documentType: item.document_type,
        createdAt: item.created_at,
        updatedAt: item.updated_at,
        lastError: item.last_error,
        progress: calculateProgress(item)
      })),
      lastKey: result.LastEvaluatedKey ? 
        encodeURIComponent(JSON.stringify(result.LastEvaluatedKey)) : null,
      count: result.Count
    });
    
  } catch (error) {
    console.error('Error getting documents:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

/**
 * GET /api/documents/:documentId/results
 * Get LLM processing results for a document
 */
app.get('/api/documents/:documentId/results', async (req, res) => {
  try {
    const { documentId } = req.params;
    
    const document = await getDocument(documentId);
    
    if (!document) {
      return res.status(404).json({ error: 'Document not found' });
    }

    if (!document.result_key) {
      return res.status(404).json({ error: 'No results available' });
    }

    // Get results from S3
    const s3 = new AWS.S3();
    const result = await s3.getObject({
      Bucket: process.env.S3_BUCKET || 'docproc-bucket',
      Key: document.result_key
    }).promise();

    const results = JSON.parse(result.Body.toString());
    
    res.json({
      documentId,
      results,
      resultKey: document.result_key
    });
    
  } catch (error) {
    console.error('Error getting document results:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

/**
 * GET /api/documents/:documentId/files
 * Get file information for a document
 */
app.get('/api/documents/:documentId/files', async (req, res) => {
  try {
    const { documentId } = req.params;
    
    const document = await getDocument(documentId);
    
    if (!document) {
      return res.status(404).json({ error: 'Document not found' });
    }

    res.json({
      documentId,
      originalFiles: document.pages || [],
      movedFiles: document.moved_files || [],
      ocrTextFiles: document.ocr_text_keys || [],
      combinedTextFile: document.combined_key,
      resultFile: document.result_key
    });
    
  } catch (error) {
    console.error('Error getting document files:', error);
    res.status(500).json({ error: 'Internal server error' });
  }
});

// Helper functions

async function getDocument(documentId) {
  const params = {
    TableName: 'docproc-documents',
    Key: { document_id: documentId }
  };
  
  const result = await dynamodb.get(params).promise();
  return result.Item;
}

async function buildDocumentTimeline(document) {
  const timeline = [];
  
  // File Upload
  timeline.push({
    step: 'File Upload',
    status: 'completed',
    timestamp: document.created_at,
    details: `File: ${document.original_filename || 'Unknown'}`
  });

  // ZIP Extraction (if applicable)
  if (document.zip_extraction) {
    timeline.push({
      step: 'ZIP Extraction',
      status: 'completed',
      details: `Extracted ${document.zip_extraction.extracted_count || 0} files`
    });
  }

  // Workflow Triggered
  if (document.step_function_execution_arn) {
    timeline.push({
      step: 'Workflow Triggered',
      status: 'completed',
      timestamp: document.created_at,
      details: 'Step Functions execution started'
    });
  }

  // OCR Processing
  if (document.textract_jobs) {
    const ocrStatuses = Object.values(document.textract_jobs);
    const hasFailures = ocrStatuses.some(status => status === 'FAILED');
    const allComplete = ocrStatuses.every(status => 
      status === 'SYNC_COMPLETE' || status === 'ASYNC_COMPLETE'
    );
    
    timeline.push({
      step: 'OCR Processing',
      status: hasFailures ? 'failed' : (allComplete ? 'completed' : 'in_progress'),
      details: getOCRDetails(document.textract_jobs)
    });
  }

  // Text Aggregation
  if (document.combined_key) {
    timeline.push({
      step: 'Text Aggregation',
      status: 'completed',
      details: 'Text from all pages combined'
    });
  }

  // LLM Processing
  if (document.result_key || document.status === 'LLM_RUNNING') {
    const llmStatus = document.status === 'COMPLETE' ? 'completed' : 
                     document.status === 'FAILED' ? 'failed' : 'in_progress';
    
    timeline.push({
      step: 'LLM Processing',
      status: llmStatus,
      details: document.document_type ? 
        `Classified as: ${document.document_type}` : 
        'Classification in progress'
    });
  }

  // File Movement
  if (document.moved_files && document.moved_files.length > 0) {
    timeline.push({
      step: 'File Movement',
      status: 'completed',
      details: `Moved ${document.moved_files.length} file(s) to complete folder`
    });
  }

  // Add error details if failed
  if (document.status === 'FAILED' && document.last_error) {
    const failedStep = timeline.find(step => step.status === 'failed');
    if (failedStep) {
      failedStep.error = document.last_error;
      failedStep.errorType = categorizeError(document.last_error);
    }
  }

  return timeline;
}

function buildTimelineSummary(timeline, document) {
  const completedSteps = timeline.filter(step => step.status === 'completed').length;
  const totalSteps = timeline.length;
  const failedSteps = timeline.filter(step => step.status === 'failed').length;
  
  return {
    totalSteps,
    completedSteps,
    failedSteps,
    progress: totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0,
    overallStatus: document.status,
    documentType: document.document_type,
    processingTime: calculateProcessingTime(document)
  };
}

function calculateProgress(document) {
  const steps = [
    'File Upload',
    'Workflow Triggered',
    'OCR Processing',
    'Text Aggregation',
    'LLM Processing',
    'File Movement'
  ];
  
  let completedSteps = 0;
  
  // File Upload (always completed if document exists)
  completedSteps++;
  
  // Workflow Triggered
  if (document.step_function_execution_arn) {
    completedSteps++;
  }
  
  // OCR Processing
  if (document.textract_jobs) {
    const ocrStatuses = Object.values(document.textract_jobs);
    const allComplete = ocrStatuses.every(status => 
      status === 'SYNC_COMPLETE' || status === 'ASYNC_COMPLETE'
    );
    if (allComplete) completedSteps++;
  }
  
  // Text Aggregation
  if (document.combined_key) {
    completedSteps++;
  }
  
  // LLM Processing
  if (document.result_key || document.status === 'COMPLETE') {
    completedSteps++;
  }
  
  // File Movement
  if (document.moved_files && document.moved_files.length > 0) {
    completedSteps++;
  }
  
  return Math.round((completedSteps / steps.length) * 100);
}

function getOCRDetails(textractJobs) {
  const jobs = Object.entries(textractJobs);
  const syncJobs = jobs.filter(([_, status]) => status === 'SYNC_COMPLETE').length;
  const asyncJobs = jobs.filter(([_, status]) => 
    status === 'ASYNC_STARTED' || status === 'ASYNC_COMPLETE'
  ).length;
  
  if (syncJobs > 0 && asyncJobs > 0) {
    return `Mixed processing: ${syncJobs} synchronous, ${asyncJobs} asynchronous`;
  } else if (syncJobs > 0) {
    return `Synchronous processing: ${syncJobs} page(s)`;
  } else if (asyncJobs > 0) {
    return `Asynchronous processing: ${asyncJobs} page(s)`;
  } else {
    return 'OCR processing';
  }
}

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
    return 'Unknown error';
  }
}

function calculateProcessingTime(document) {
  if (document.created_at && document.updated_at) {
    const start = new Date(document.created_at);
    const end = new Date(document.updated_at);
    return Math.round((end - start) / 1000); // Duration in seconds
  }
  return null;
}

// Start server
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Document processing API server running on port ${PORT}`);
});

module.exports = app;
