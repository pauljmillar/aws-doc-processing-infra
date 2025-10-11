# Output values from the Terraform configuration
# Bucket names, ARNs, and other important values

output "bucket_name" {
  value = aws_s3_bucket.docproc.bucket
}

output "queue_url" {
  value = aws_sqs_queue.incoming.id
}

output "dynamodb_table" {
  value = aws_dynamodb_table.documents.name
}

output "step_function_arn" {
  value = aws_sfn_state_machine.workflow.arn
}
