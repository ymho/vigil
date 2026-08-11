#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 S3_BUCKET_NAME" >&2
  exit 2
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
BUNDLE="$REPO_ROOT/artifacts/vigil-bundle.tar.gz"
KEY=${VIGIL_BUNDLE_KEY:-bootstrap/vigil-bundle.tar.gz}

if [[ ! -f "$BUNDLE" ]]; then
  echo "Bundle not found: $BUNDLE" >&2
  echo "Run scripts/prepare_bundle.sh first." >&2
  exit 1
fi

aws s3 cp "$BUNDLE" "s3://$1/$KEY" --sse AES256
aws s3 ls "s3://$1/$KEY"
