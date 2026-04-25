"""Unit tests for catfish-policy matchers.

Run with:
    cd /Users/chenhongbo/person_task/catfish/plugins/catfish-policy
    python -m pytest test_matchers.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from matchers import match_rules  # noqa: E402


# ---------------------------------------------------------------------------
# shell_command
# ---------------------------------------------------------------------------

RULES_SHELL = {
    "rules": [{
        "id": "no-rm-rf-root",
        "kind": "shell_command",
        "match": ["rm -rf /", "rm -rf /*"],
        "reason": "destructive",
    }]
}


def test_block_rm_rf_root():
    hit = match_rules("bash", {"command": "rm -rf / --no-preserve-root"}, RULES_SHELL)
    assert hit is not None
    assert hit["id"] == "no-rm-rf-root"


def test_allow_normal_rm():
    hit = match_rules("bash", {"command": "rm -rf ./tmp"}, RULES_SHELL)
    assert hit is None


def test_block_via_different_arg_key():
    hit = match_rules("execute_code", {"code": "import os; os.system('rm -rf /')"}, RULES_SHELL)
    assert hit is not None


def test_non_shell_tool_ignored():
    hit = match_rules("read_file", {"path": "rm -rf /"}, RULES_SHELL)
    assert hit is None


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------

RULES_FILE = {
    "rules": [{
        "id": "no-ssh-key",
        "kind": "file_read",
        "paths": ["~/.ssh/id_*", "~/.ssh/*_rsa"],
        "reason": "ssh key",
    }]
}


def test_block_ssh_private_key():
    hit = match_rules("read_file", {"path": "~/.ssh/id_rsa"}, RULES_FILE)
    assert hit is not None


def test_allow_ssh_config():
    hit = match_rules("read_file", {"path": "~/.ssh/config"}, RULES_FILE)
    assert hit is None


# ---------------------------------------------------------------------------
# network_request
# ---------------------------------------------------------------------------

RULES_NET = {
    "rules": [{
        "id": "no-rogue-openai",
        "kind": "network_request",
        "deny_hosts": ["api.openai.com"],
        "reason": "bypass gateway",
    }]
}


def test_block_direct_openai():
    hit = match_rules("fetch_url", {"url": "https://api.openai.com/v1/chat"}, RULES_NET)
    assert hit is not None


def test_block_embedded_curl():
    hit = match_rules("bash", {"command": "curl https://api.openai.com/v1/models"}, RULES_NET)
    assert hit is not None


def test_allow_gateway_url():
    hit = match_rules("fetch_url", {"url": "http://localhost:8999/v1/chat"}, RULES_NET)
    assert hit is None


# ---------------------------------------------------------------------------
# empty / no-match
# ---------------------------------------------------------------------------

def test_empty_ruleset():
    hit = match_rules("bash", {"command": "rm -rf /"}, {"rules": []})
    assert hit is None


def test_unknown_kind_doesnt_crash():
    hit = match_rules("bash", {"command": "ls"}, {"rules": [{"id": "x", "kind": "unknown"}]})
    assert hit is None


if __name__ == "__main__":
    # Fallback runner if pytest isn't installed
    import inspect
    tests = [obj for name, obj in globals().items()
             if name.startswith("test_") and inspect.isfunction(obj)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
