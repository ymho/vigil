#!/usr/bin/env bash
set -euo pipefail

exec /opt/vigil/bin/vigil-agent \
  "このTerraform環境からInternetへ出られる経路が存在するか調査してください。Terraformの構成だけで断定せず実通信も確認してください。一方で、許可されたS3への通信は利用できるか確認し、最後に観測した根拠だけを箇条書きにしてください。"
