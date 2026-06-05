#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found; install Python 3.11+ or set PYTHON=/path/to/python" >&2
  exit 1
fi

if ! "$PYTHON" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  echo "error: Python 3.11+ is required" >&2
  exit 1
fi

cd "$REPO_ROOT"
"$PYTHON" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
mkdir -p data

if [ ! -f .env.native ]; then
  cat > .env.native <<'EOF'
# AgentSearch native runtime config.
# You still need a running SearXNG instance with JSON search enabled.
SEARXNG_URL=http://localhost:8080
HOST=127.0.0.1
PORT=3939
DATA_DIR=./data
ADAPTERS_DIR=./adapters
# AGENT_SEARCH_TOKEN=
EOF
fi

echo "Native install complete."
echo "Edit .env.native if needed, then run: ./scripts/run-native.sh"
