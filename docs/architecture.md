# Architecture notes

## Closed network

The VPC intentionally has no Internet Gateway and no NAT Gateway. The private route table has no default route to the Internet.

## Systems Manager

SSM Agent initiates outbound HTTPS connections to Systems Manager. Interface VPC endpoints provide private IP paths for `ssm`, `ssmmessages`, and `ec2messages`. The EC2 security group has no inbound rules. The VPC endpoint security group accepts 443 only from the EC2 security group.

## S3 ingress

The offline bundle is uploaded from an administrator workstation to an encrypted, non-public S3 bucket. The private EC2 instance accesses that bucket through the S3 Gateway VPC Endpoint. The endpoint policy and EC2 role restrict access to the artifact bucket.

## Local LLM

The bundle contains an Ollama Linux runtime and a user-supplied GGUF. Bootstrap imports the GGUF with a local `Modelfile`; it does not use `ollama pull` from the private EC2 instance.

## Agent loop

```text
LLM decides next action
       |
       v
Agent Runtime executes Tool
       |
       v
Tool result returned to LLM
       |
       +---- repeat until final answer
```

The implementation intentionally avoids arbitrary shell execution. Runtime network checks are exposed as narrow, explicit functions.
