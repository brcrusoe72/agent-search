"""Launch the real browser with deployment restrictions and no network access."""
import argparse
from pathlib import Path
import subprocess


PROBE = """
import asyncio
import os
from playwright.async_api import async_playwright

async def render():
    assert os.getuid() != 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path='/usr/bin/chromium', headless=True,
            chromium_sandbox=True, args=['--disable-dev-shm-usage'],
        )
        try:
            page = await browser.new_page()
            await page.set_content('<title>Sandbox check</title><p>Ready</p>')
            assert await page.title() == 'Sandbox check'
            print('Non-root sandboxed browser rendered successfully')
        finally:
            await browser.close()

async def run():
    await asyncio.gather(render(), render())

asyncio.run(run())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agent-search-browser:test')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    subprocess.run([
        'docker', 'run', '--rm', '--init', '--network', 'none', '--read-only',
        '--tmpfs', '/tmp:size=256m,nosuid', '--cap-drop', 'ALL',
        '--cap-add', 'SYS_CHROOT', '--security-opt', 'no-new-privileges:true',
        '--security-opt', f'seccomp={root / "examples/chromium-seccomp.json"}',
        '--pids-limit', '512', '--memory', '768m', '--cpus', '1',
        '-e', 'HOME=/tmp/browser-home', '-e', 'XDG_CACHE_HOME=/tmp/browser-cache',
        '-e', 'XDG_CONFIG_HOME=/tmp/browser-config', '--entrypoint', 'python',
        args.image, '-c', PROBE,
    ], check=True, timeout=60)


if __name__ == '__main__':
    main()
