#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
OUT="$SCRIPT_DIR/../openapi.json"
URL="${OPENAPI_URL:-http://localhost:8000/openapi.json}"
curl -fsSL "$URL" -o "$OUT"
echo "Wrote $OUT"
