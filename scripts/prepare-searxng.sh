#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

create_settings() {
  local example_path="$1"
  local target_path="$2"

  if [[ -f "$target_path" ]]; then
    echo "exists: ${target_path#$ROOT_DIR/}"
    return
  fi

  local secret
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

  python3 - "$example_path" "$target_path" "$secret" <<'PY'
from pathlib import Path
import os
import sys

example = Path(sys.argv[1])
target = Path(sys.argv[2])
secret = sys.argv[3]

text = example.read_text(encoding="utf-8")
text = text.replace("AGENTSEARCH_GENERATE_LOCAL_SECRET", secret)
text = text.replace("CHANGE_ME_GENERATE_A_RANDOM_KEY", secret)

target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(text, encoding="utf-8")
os.chmod(target, 0o600)
PY

  echo "created: ${target_path#$ROOT_DIR/}"
}

create_settings "$ROOT_DIR/searxng/settings.example.yml" "$ROOT_DIR/searxng/settings.yml"
create_settings "$ROOT_DIR/examples/settings.tor.yml" "$ROOT_DIR/searxng/settings.tor.yml"
