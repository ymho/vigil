#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTANCE_ID=$(terraform -chdir="$REPO_ROOT/infra" output -raw instance_id)
exec aws ssm start-session --target "$INSTANCE_ID"
