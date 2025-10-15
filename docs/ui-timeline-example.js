// Example JavaScript code for UI app to build processing timeline
// This can be adapted to your specific UI framework (React, Vue, Angular, etc.)

class DocumentTimelineBuilder {
  constructor(awsConfig) {
    this.dynamodb = new AWS.DynamoDB.DocumentClient(awsConfig);
    this.stepFunctions = new AWS.StepFunctions(awsConfig);
  }

  /**
   * Build a processing timeline for a document
   * @param {string} documentId - The document ID
   * @returns {Promise<Array>} Timeline of processing steps
   */
  async buildTimeline(documentId) {
    try {
      // Get document from DynamoDB
      const document = await this.getDocument(documentId);
      
      if (!document) {
        throw new Error(`Document ${documentId} not found`);
      }

      // Build basic timeline from DynamoDB fields
      const timeline = this.buildBasicTimeline(document);
      
      // Enhance with Step Functions details if available
      if (document.step_function_execution_arn) {
        await this.enhanceWithStepFunctions(timeline, document.step_function_execution_arn);
      }
      
      // Add error details if failed
      this.addErrorDetails(timeline, document);
      
      return timeline;
    } catch (error) {
      console.error('Error building timeline:', error);
      return [{
        step: 'Error',
        status: 'failed',
        details: `Failed to build timeline: ${error.message}`,
        timestamp: new Date().toISOString()
      }];
    }
  }

  /**
   * Get document from DynamoDB
   */
  async getDocument(documentId) {
    const params = {
      TableName: 'docproc-documents',
      Key: { document_id: documentId }
    };
    
    const result = await this.dynamodb.get(params).promise();
    return result.Item;
  }

  /**
   * Build basic timeline from DynamoDB fields
   */
  buildBasicTimeline(document) {
    const timeline = [];
    
    // File Upload (always present)
    timeline.push({
      step: 'File Upload',
      status: 'completed',
      timestamp: document.created_at,
      details: `File: ${document.original_filename || 'Unknown'}`
    });

    // Check if it's a ZIP file (has zip_extraction field)
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

    return timeline;
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
   * Enhance timeline with Step Functions execution details
   */
  async enhanceWithStepFunctions(timeline, executionArn) {
    try {
      const history = await this.getStepFunctionsHistory(executionArn);
      
      // Map Step Functions states to timeline steps
      const stateMapping = {
        'OCR': 'OCR Processing',
        'AggregateText': 'Text Aggregation',
        'LLM': 'LLM Processing'
      };
      
      // Update timeline with Step Functions timing
      timeline.forEach(step => {
        const sfStep = history.find(sf => 
          stateMapping[sf.state] === step.step
        );
        
        if (sfStep) {
          step.stepFunctionDetails = {
            state: sfStep.state,
            timestamp: sfStep.timestamp,
            duration: sfStep.duration
          };
        }
      });
    } catch (error) {
      console.warn('Could not get Step Functions history:', error);
    }
  }

  /**
   * Get Step Functions execution history
   */
  async getStepFunctionsHistory(executionArn) {
    const params = {
      executionArn: executionArn
    };
    
    const result = await this.stepFunctions.getExecutionHistory(params).promise();
    
    return result.events
      .filter(event => ['TaskStateEntered', 'TaskSucceeded', 'TaskFailed'].includes(event.type))
      .map(event => ({
        type: event.type,
        state: event.stateEnteredEventDetails?.name,
        timestamp: event.timestamp,
        duration: this.calculateDuration(event)
      }));
  }

  /**
   * Calculate step duration
   */
  calculateDuration(event) {
    if (event.taskSucceededEventDetails && event.taskStartedEventDetails) {
      const start = new Date(event.taskStartedEventDetails.timestamp);
      const end = new Date(event.timestamp);
      return Math.round((end - start) / 1000); // Duration in seconds
    }
    return null;
  }

  /**
   * Add error details to timeline
   */
  addErrorDetails(timeline, document) {
    if (document.status === 'FAILED' && document.last_error) {
      // Find the step that failed
      const failedStep = timeline.find(step => step.status === 'failed');
      if (failedStep) {
        failedStep.error = document.last_error;
        failedStep.errorType = this.categorizeError(document.last_error);
      }
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
}

// Example usage in a React component
class DocumentTimeline extends React.Component {
  constructor(props) {
    super(props);
    this.timelineBuilder = new DocumentTimelineBuilder({
      region: 'us-west-2',
      accessKeyId: process.env.REACT_APP_AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.REACT_APP_AWS_SECRET_ACCESS_KEY
    });
    
    this.state = {
      timeline: [],
      loading: true,
      error: null
    };
  }

  async componentDidMount() {
    try {
      const timeline = await this.timelineBuilder.buildTimeline(this.props.documentId);
      this.setState({ timeline, loading: false });
    } catch (error) {
      this.setState({ error: error.message, loading: false });
    }
  }

  render() {
    if (this.state.loading) {
      return <div>Loading timeline...</div>;
    }

    if (this.state.error) {
      return <div>Error: {this.state.error}</div>;
    }

    return (
      <div className="timeline">
        <h3>Processing Timeline</h3>
        {this.state.timeline.map((step, index) => (
          <div key={index} className={`timeline-step ${step.status}`}>
            <div className="step-header">
              <span className="step-name">{step.step}</span>
              <span className={`step-status ${step.status}`}>
                {step.status === 'completed' ? '✓' : 
                 step.status === 'failed' ? '✗' : '⏳'}
              </span>
            </div>
            <div className="step-details">{step.details}</div>
            {step.error && (
              <div className="step-error">
                <strong>Error:</strong> {step.errorType || step.error}
              </div>
            )}
            {step.stepFunctionDetails && (
              <div className="step-timing">
                Duration: {step.stepFunctionDetails.duration}s
              </div>
            )}
          </div>
        ))}
      </div>
    );
  }
}

// Example CSS for timeline display
const timelineStyles = `
.timeline {
  padding: 20px;
}

.timeline-step {
  margin-bottom: 15px;
  padding: 10px;
  border-left: 3px solid #ddd;
  padding-left: 15px;
}

.timeline-step.completed {
  border-left-color: #28a745;
}

.timeline-step.failed {
  border-left-color: #dc3545;
}

.timeline-step.in_progress {
  border-left-color: #ffc107;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 5px;
}

.step-name {
  font-weight: bold;
}

.step-status {
  font-size: 18px;
}

.step-details {
  color: #666;
  font-size: 14px;
}

.step-error {
  color: #dc3545;
  font-size: 14px;
  margin-top: 5px;
}

.step-timing {
  color: #999;
  font-size: 12px;
  margin-top: 5px;
}
`;

export default DocumentTimeline;
