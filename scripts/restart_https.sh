#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
scripts/clean_8443.sh || true
uvicorn webapp.asgi:application --host 0.0.0.0 --port 8443 \
  --ssl-certfile ./face.syncrobotic.ai/fullchain.crt \
  --ssl-keyfile ./face.syncrobotic.ai/private.key
