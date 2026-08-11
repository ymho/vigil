#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/app"

rm -rf "$OUT"
mkdir -p "$OUT"

tar -czf "$OUT/vigil-app.tar.gz" \
  -C "$ROOT" \
  agent \
  infra