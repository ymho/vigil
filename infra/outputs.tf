output "instance_id" {
  value       = aws_instance.agent.id
  description = "Private EC2 instance ID"
}

output "artifact_bucket_name" {
  value       = aws_s3_bucket.artifacts.bucket
  description = "S3 bucket used as controlled offline ingress"
}

output "runtime_s3_uri" {
  description = "S3 URI for the Ollama runtime"
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${var.runtime_object_key}"
}

output "model_s3_uri" {
  description = "S3 URI for the GGUF model"
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${var.model_object_key}"
}

output "app_s3_uri" {
  description = "S3 URI for the VIGIL application bundle"
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${var.app_object_key}"
}

output "ssm_start_session_command" {
  value       = "aws ssm start-session --target ${aws_instance.agent.id}"
  description = "Connect without SSH/Public IP"
}
