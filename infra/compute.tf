resource "aws_instance" "agent" {
  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.private.id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = var.root_volume_size_gib
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    artifact_bucket   = aws_s3_bucket.artifacts.bucket
    bundle_object_key = var.bundle_object_key
    model_name        = var.model_name
  })

  tags = {
    Name = "${var.project_name}-agent"
  }

  depends_on = [
    aws_vpc_endpoint.interface,
    aws_vpc_endpoint.s3,
  ]
}
