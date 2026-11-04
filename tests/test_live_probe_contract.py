"""An unavailable test runner must never count as verified network isolation."""
from pathlib import Path
import runpy
import subprocess

import pytest


@pytest.mark.parametrize('returncode,output', [
    (1, ''),
    (0, '{"blocked": false}'),
    (0, '{"blocked": false, "errno": null}'),
])
def test_egress_check_rejects_failed_execution_or_inconclusive_connection(monkeypatch, returncode, output):
    check = runpy.run_path(str(Path(__file__).with_name('test_live_docker.py')))['_assert_no_direct_egress']
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, returncode, output, 'probe unavailable'))
    with pytest.raises(AssertionError):
        check(('api',))


def test_egress_check_accepts_executed_network_denial(monkeypatch):
    check = runpy.run_path(str(Path(__file__).with_name('test_live_docker.py')))['_assert_no_direct_egress']
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '{"blocked": true, "errno": 101}', ''))
    check(('api',))
