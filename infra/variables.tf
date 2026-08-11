variable "aws_region" {
  description = "AWS Region"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "Name prefix"
  type        = string
  default     = "vigil"
}

variable "vpc_cidr" {
  description = "VPC CIDR"
  type        = string
  default     = "10.40.0.0/16"
}

variable "private_subnet_cidr" {
  description = "Private subnet CIDR"
  type        = string
  default     = "10.40.1.0/24"
}

variable "availability_zone" {
  description = "AZ. Empty means first available AZ in the Region."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "EC2 instance type. CPU-only demo default; increase memory for larger models."
  type        = string
  default     = "t3.xlarge"
}

variable "root_volume_size_gib" {
  description = "Encrypted root EBS size. Must fit the Ollama runtime, GGUF model and imported model."
  type        = number
  default     = 100
}

variable "runtime_object_key" {
  description = "S3 object key containing the Ollama runtime"
  type        = string
  default     = "runtime/ollama-root.tar.gz"
}

variable "model_object_key" {
  description = "S3 object key containing the GGUF model"
  type        = string
  default     = "runtime/model.gguf"
}

variable "app_object_key" {
  description = "S3 object key containing the VIGIL application"
  type        = string
  default     = "app/vigil-app.tar.gz"
}

variable "model_name" {
  description = "Ollama model name created from the bundled GGUF"
  type        = string
  default     = "vigil-agent"
}
