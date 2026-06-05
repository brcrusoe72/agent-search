#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [ ! -d .venv ]; then
  echo "error: .venv not found; run ./scripts/install-native.sh first" >&2
  exit 1
fi

set -a
if [ -f .env.native ]; then
  . ./.env.native
fi
set +a

. .venv/bin/activate
exec agent-search \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-3939}" \
  --searxng-url "${SEARXNG_URL:-http://localhost:8080}" \
  --data-dir "${DATA_DIR:-./data}" \
  --adapters-dir "${ADAPTERS_DIR:-./adapters}"
