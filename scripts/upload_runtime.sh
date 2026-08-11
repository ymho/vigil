#!/bin/bash
set -euo pipefail

BUCKET="$1"
MODEL="$2"

aws s3 cp \
  dist/runtime/ollama-root.tar.gz \
  "s3://${BUCKET}/runtime/ollama-root.tar.gz"

aws s3 cp \
  "$MODEL" \
  "s3://${BUCKET}/runtime/model.gguf"