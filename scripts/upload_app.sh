#!/bin/bash
set -euo pipefail

BUCKET="$1"

aws s3 cp \
  dist/app/vigil-app.tar.gz \
  "s3://${BUCKET}/app/vigil-app.tar.gz"