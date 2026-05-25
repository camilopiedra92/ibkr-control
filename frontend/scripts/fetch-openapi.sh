#!/bin/sh
set -eu
# POSIX-portable: works in Alpine /bin/sh, Debian bash, macOS bash. Don't
# add bash-isms — this script must run inside the frontend container.
# Uses wget (available on node:22-alpine); curl is not present in Alpine by default.
SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
OUT="$SCRIPT_DIR/../openapi.json"
URL="${OPENAPI_URL:-http://backend:8000/openapi.json}"
wget -qO "$OUT" "$URL"
echo "Wrote $OUT"
