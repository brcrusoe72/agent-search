#!/usr/bin/env python3
"""One-time migration of existing Docker data volumes to runtime UID/GID 10001.

Stop API writers first. This changes ownership only, never deletes data, and
refuses nonexistent volume names. The short-lived helper has no network and
a read-only root filesystem; application containers remain non-root.
"""
import argparse
import re
import subprocess

DEFAULT_IMAGE = 'python@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab'
MIGRATION = '''
import os
for directory, dirs, files in os.walk('/data', topdown=False, followlinks=False):
    for name in files + dirs:
        os.chown(os.path.join(directory, name), 10001, 10001, follow_symlinks=False)
    os.chown(directory, 10001, 10001, follow_symlinks=False)
print('Data volume ownership migrated to 10001:10001; files retained.')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--volume', action='append', required=True,
                        help='existing named volume; repeat for direct/private volumes')
    parser.add_argument('--image', default=DEFAULT_IMAGE, help='local Python-capable helper image')
    args = parser.parse_args()
    for volume in args.volume:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', volume):
            parser.error('Invalid Docker volume name')
        subprocess.run(['docker', 'volume', 'inspect', volume], check=True,
                       stdout=subprocess.DEVNULL)
    for volume in args.volume:
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                        '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
                        '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges',
                        '--mount', f'type=volume,src={volume},dst=/data',
                        args.image, 'python', '-c', MIGRATION], check=True)


if __name__ == '__main__':
    main()
