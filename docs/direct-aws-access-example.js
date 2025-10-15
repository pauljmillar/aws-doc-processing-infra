// Example: Direct AWS access from your UI app
// This is what you can implement right now without building a backend

const AWS = require('aws-sdk');

// Configure AWS (you'll need to set up credentials)
AWS.config.update({
  region: 'us-west-2',
  // Add your AWS credentials here or use IAM roles
});

const dynamodb = new AWS.DynamoDB.DocumentClient();
const s3 = new AWS.S3();

class DocumentService {
  /**
   * Get a single document with its processing timeline
   */
  async getDocument(documentId) {
    try {
      // Get document from DynamoDB
      const document = await this.getDocumentFromDynamoDB(documentId);
      
      if (!document) {
        throw new Error('Document not found');
      }

      // Build timeline from DynamoDB fields
      const timeline = this.buildTimeline(document);
      
      return {
        documentId,
        document,
        timeline,
        summary: this.buildSummary(document, timeline)
      };
    } catch (error) {
      console.error('Error getting document:', error);
      throw error;
    }
  }

  /**
   * List all documents with pagination
   */
  async listDocuments(options = {}) {
    const {
      limit = 50,
      lastKey,
      status,
      sortBy = 'created_at',
      sortOrder = 'desc'
    } = options;

    const params = {
      TableName: 'docproc-documents',
      Limit: limit
    };

    // Add filter if status specified
    if (status) {
      params.FilterExpression = '#status = :status';
      params.ExpressionAttributeNames = { '#status': 'status' };
      params.ExpressionAttributeValues = { ':status': status };
    }

    // Add pagination
    if (lastKey) {
      params.ExclusiveStartKey = lastKey;
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

    return {
      documents: sortedItems.map(item => this.formatDocumentSummary(item)),
      lastKey: result.LastEvaluatedKey,
      count: result.Count
    };
  }

  /**
   * Get LLM results for a document
   */
  async getDocumentResults(documentId) {
    const document = await this.getDocumentFromDynamoDB(documentId);
    
    if (!document) {
      throw new Error('Document not found');
    }

    if (!document.result_key) {
      throw new Error('No results available');
    }

    // Get results from S3
    const result = await s3.getObject({
      Bucket: 'docproc-bucket',
      Key: document.result_key
    }).promise();

    return JSON.parse(result.Body.toString());
  }

  /**
   * Get document from DynamoDB
   */
  async getDocumentFromDynamoDB(documentId) {
    const params = {
      TableName: 'docproc-documents',
      Key: { document_id: documentId }
    };
    
    const result = await dynamodb.get(params).promise();
    return result.Item;
  }

  /**
   * Build processing timeline from DynamoDB fields
   */
  buildTimeline(document) {
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
        details: this.getOCRDetails(document.textract_jobs)
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
        failedStep.errorType = this.categorizeError(document.last_error);
      }
    }

    return timeline;
  }

  /**
   * Build summary information
   */
  buildSummary(document, timeline) {
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
      processingTime: this.calculateProcessingTime(document)
    };
  }

  /**
   * Format document for summary view
   */
  formatDocumentSummary(document) {
    return {
      documentId: document.document_id,
      status: document.status,
      originalFilename: document.original_filename,
      documentType: document.document_type,
      createdAt: document.created_at,
      updatedAt: document.updated_at,
      lastError: document.last_error,
      progress: this.calculateProgress(document)
    };
  }

  /**
   * Calculate processing progress percentage
   */
  calculateProgress(document) {
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

  /**
   * Get OCR processing details
   */
  getOCRDetails(textractJobs) {
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

  /**
   * Categorize error messages
   */
  categorizeError(errorMessage) {
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

  /**
   * Calculate processing time
   */
  calculateProcessingTime(document) {
    if (document.created_at && document.updated_at) {
      const start = new Date(document.created_at);
      const end = new Date(document.updated_at);
      return Math.round((end - start) / 1000); // Duration in seconds
    }
    return null;
  }
}

// Example usage in your UI app
const documentService = new DocumentService();

// Get a specific document with timeline
documentService.getDocument('b0c121a8-9224-4637-9528-b066e492bca3')
  .then(result => {
    console.log('Document:', result.document);
    console.log('Timeline:', result.timeline);
    console.log('Summary:', result.summary);
  })
  .catch(error => {
    console.error('Error:', error);
  });

// List all documents
documentService.listDocuments({ limit: 10, status: 'COMPLETE' })
  .then(result => {
    console.log('Documents:', result.documents);
  })
  .catch(error => {
    console.error('Error:', error);
  });

// Get LLM results
documentService.getDocumentResults('b0c121a8-9224-4637-9528-b066e492bca3')
  .then(results => {
    console.log('LLM Results:', results);
  })
  .catch(error => {
    console.error('Error:', error);
  });

module.exports = DocumentService;
