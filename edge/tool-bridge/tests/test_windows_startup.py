"""Import the real startup chain with Unix-only modules unavailable."""
import os
from pathlib import Path
import subprocess
import sys


def test_server_import_without_unix_modules():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    script = """
import sys
for name in ('resource', 'fcntl', 'pwd', 'grp', 'termios'):
    sys.modules[name] = None
from catfish_tool_bridge import server, sandbox
assert callable(server.init_and_serve)
sandbox.detect_sandbox_kind = lambda: None
result = sandbox.run_in_sandbox('raise AssertionError("must not execute")')
assert result['ok'] is False and result['sandbox_used'] is False
"""
    result = subprocess.run([sys.executable, '-c', script], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_enabled_sandbox_never_falls_back_to_unsandboxed_registry(monkeypatch):
    import asyncio
    from catfish_tool_bridge import adapter, sandbox
    monkeypatch.setenv('CATFISH_SANDBOX_EXEC', '1')
    monkeypatch.setattr(sandbox, 'detect_sandbox_kind', lambda: None)
    def no_registry():
        raise AssertionError('must not bypass required isolation')
    monkeypatch.setattr(adapter, '_r', no_registry)
    result = asyncio.run(adapter._do_dispatch('execute_code', {'code': 'print(1)'}))
    assert result['ok'] is False
    assert result['result']['sandbox_used'] is False
