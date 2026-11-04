#!/usr/bin/env python3
"""Load both bundled configurations in the pinned image, without network access.

This checks engine registration, not external search availability or quality.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def check(template: str, image: str) -> None:
    source = (ROOT / template).read_text()
    expected = set(re.findall(r'^  - name: (.+)$', source, re.MULTILINE))
    name = 'agentsearch-engine-check-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory() as directory:
        settings = Path(directory) / 'settings.yml'
        settings.write_text(source.replace('AGENTSEARCH_GENERATE_LOCAL_SECRET', secrets.token_urlsafe(48))
                            .replace('CHANGE_ME_GENERATE_A_RANDOM_KEY', secrets.token_urlsafe(48)))
        try:
            subprocess.run(['docker', 'create', '--name', name, '--network', 'none',
                            '--memory', '512m', '--cpus', '1', '--pids-limit', '256',
                            image], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(['docker', 'cp', str(settings), f'{name}:/etc/searxng/settings.yml'], check=True)
            subprocess.run(['docker', 'start', name], check=True, stdout=subprocess.DEVNULL)
            deadline = time.monotonic() + 45
            result = None
            while time.monotonic() < deadline:
                result = subprocess.run(['docker', 'exec', name, 'python', '-c',
                                         'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/config", timeout=3).read().decode())'],
                                        capture_output=True, text=True, timeout=8)
                if result.returncode == 0:
                    break
                time.sleep(1)
            if result is None or result.returncode:
                raise RuntimeError(f'{template}: SearXNG did not serve its configuration')
            config = json.loads(result.stdout)
            enabled = {engine['name'] for engine in config['engines'] if engine.get('enabled')}
            missing = sorted(expected - enabled)
            if missing:
                raise RuntimeError(f'{template}: configured engines missing or disabled: {missing}')
            print(f'{template}: all {len(expected)} configured engines registered and enabled', flush=True)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    image = next(line.split('=', 1)[1] for line in (ROOT / '.env.example').read_text().splitlines()
                 if line.startswith('SEARXNG_IMAGE='))
    for template in ('searxng/settings.example.yml', 'examples/settings.tor.yml'):
        check(template, image)
