#!/bin/bash
set -euo pipefail

OUT_DIR="${1:-dist/runtime}"

mkdir -p "$OUT_DIR"

curl -L \
  https://ollama.com/download/ollama-linux-amd64.tar.zst \
  -o "$OUT_DIR/ollama-linux-amd64.tar.zst"

mkdir -p "$OUT_DIR/ollama-root"

tar --zstd \
  -xf "$OUT_DIR/ollama-linux-amd64.tar.zst" \
  -C "$OUT_DIR/ollama-root"

tar -czf \
  "$OUT_DIR/ollama-root.tar.gz" \
  -C "$OUT_DIR" \
  ollama-root