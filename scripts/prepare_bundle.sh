#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/model.gguf" >&2
  exit 2
fi

MODEL_PATH=$(realpath "$1")
if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Model not found: $MODEL_PATH" >&2
  exit 2
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
ARTIFACTS="$REPO_ROOT/artifacts"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

OLLAMA_URL=${OLLAMA_URL:-https://ollama.com/download/ollama-linux-amd64.tar.zst}
OLLAMA_ARCHIVE="$STAGE/ollama.tar.zst"

mkdir -p "$ARTIFACTS" "$STAGE/bundle/ollama-root" "$STAGE/bundle/model" "$STAGE/bundle/repo"

echo "[1/4] Download Ollama Linux runtime"
curl -fL "$OLLAMA_URL" -o "$OLLAMA_ARCHIVE"

echo "[2/4] Extract Ollama runtime"
if tar --help 2>/dev/null | grep -q -- '--zstd'; then
  tar --zstd -xf "$OLLAMA_ARCHIVE" -C "$STAGE/bundle/ollama-root"
elif command -v unzstd >/dev/null 2>&1; then
  unzstd -c "$OLLAMA_ARCHIVE" | tar -xf - -C "$STAGE/bundle/ollama-root"
else
  echo "Need tar with zstd support or unzstd." >&2
  exit 1
fi

echo "[3/4] Add model and repository"
cp "$MODEL_PATH" "$STAGE/bundle/model/model.gguf"
# Avoid .git, Terraform state, downloaded artifacts and large models.
tar -C "$REPO_ROOT" \
  --exclude='.git' \
  --exclude='artifacts' \
  --exclude='.terraform' \
  --exclude='*.tfstate*' \
  --exclude='*.gguf' \
  -cf - . | tar -C "$STAGE/bundle/repo" -xf -

echo "[4/4] Create offline bundle"
tar -C "$STAGE/bundle" -czf "$ARTIFACTS/vigil-bundle.tar.gz" .
sha256sum "$ARTIFACTS/vigil-bundle.tar.gz" | tee "$ARTIFACTS/vigil-bundle.tar.gz.sha256"

echo
echo "Created: $ARTIFACTS/vigil-bundle.tar.gz"
