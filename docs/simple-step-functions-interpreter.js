// Simple Step Functions Interpreter for UI
// Copy/paste this into your UI app

const AWS = require('aws-sdk');

// Configure AWS
AWS.config.update({
  region: 'us-west-2'
});

const dynamodb = new AWS.DynamoDB.DocumentClient();
const stepFunctions = new AWS.StepFunctions();

/**
 * Get workflow steps for a document
 * @param {string} documentId - The document ID
 * @returns {Promise<Array>} Array of workflow steps with status
 */
async function getWorkflowSteps(documentId) {
  try {
    // Get document from DynamoDB
    const doc = await dynamodb.get({
      TableName: 'docproc-documents',
      Key: { document_id: documentId }
    }).promise();
    
    if (!doc.Item?.step_function_execution_arn) {
      return [{ step: 'No workflow', status: 'not_started' }];
    }
    
    // Get Step Functions execution history
    const history = await stepFunctions.getExecutionHistory({
      executionArn: doc.Item.step_function_execution_arn
    }).promise();
    
    // Extract workflow steps
    const steps = [];
    const stateEntries = history.events.filter(e => e.type === 'TaskStateEntered');
    
    for (const entry of stateEntries) {
      const stateName = entry.stateEnteredEventDetails.name;
      const timestamp = entry.timestamp;
      
      // Find if this step succeeded or failed
      const laterEvents = history.events.filter(e => 
        e.timestamp > timestamp && 
        (e.type === 'TaskSucceeded' || e.type === 'TaskFailed')
      );
      
      const status = laterEvents.length > 0 ? 
        (laterEvents[0].type === 'TaskSucceeded' ? 'completed' : 'failed') : 
        'in_progress';
      
      steps.push({
        step: stateName,
        status: status,
        timestamp: timestamp
      });
    }
    
    return steps;
  } catch (error) {
    console.error('Error getting workflow steps:', error);
    return [{ step: 'Error', status: 'failed', error: error.message }];
  }
}

/**
 * Get document status summary
 * @param {string} documentId - The document ID
 * @returns {Promise<Object>} Document status and workflow steps
 */
async function getDocumentStatus(documentId) {
  try {
    // Get document from DynamoDB
    const doc = await dynamodb.get({
      TableName: 'docproc-documents',
      Key: { document_id: documentId }
    }).promise();
    
    if (!doc.Item) {
      throw new Error('Document not found');
    }
    
    const document = doc.Item;
    
    // Get workflow steps
    const workflowSteps = await getWorkflowSteps(documentId);
    
    return {
      documentId: document.document_id,
      status: document.status,
      originalFilename: document.original_filename,
      documentType: document.document_type,
      createdAt: document.created_at,
      updatedAt: document.updated_at,
      lastError: document.last_error,
      workflowSteps: workflowSteps
    };
  } catch (error) {
    console.error('Error getting document status:', error);
    throw error;
  }
}

// Example usage:
// getWorkflowSteps('b0c121a8-9224-4637-9528-b066e492bca3')
//   .then(steps => console.log('Workflow steps:', steps))
//   .catch(error => console.error('Error:', error));

// getDocumentStatus('b0c121a8-9224-4637-9528-b066e492bca3')
//   .then(status => console.log('Document status:', status))
//   .catch(error => console.error('Error:', error));

module.exports = {
  getWorkflowSteps,
  getDocumentStatus
};
