output "instance_id" {
  value       = aws_instance.agent.id
  description = "Private EC2 instance ID"
}

output "artifact_bucket_name" {
  value       = aws_s3_bucket.artifacts.bucket
  description = "S3 bucket used as controlled offline ingress"
}

output "bundle_s3_uri" {
  value       = "s3://${aws_s3_bucket.artifacts.bucket}/${var.bundle_object_key}"
  description = "Expected offline bundle URI"
}

output "ssm_start_session_command" {
  value       = "aws ssm start-session --target ${aws_instance.agent.id}"
  description = "Connect without SSH/Public IP"
}
