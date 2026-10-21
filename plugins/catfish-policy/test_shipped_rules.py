"""对仓库里真正发出去的 rules.yaml 做测试 (10/21)。

test_matchers.py 用的是测试里手写的规则, 测不到 rules.yaml 本身写错。
10/21 回放近 30 天真实调用才发现: R6 写成 tool_name + deny_args_pattern, 匹配器
却忽略 deny_args_pattern —— 装上就会拦掉全部 141 次 skill_manage; R9 用的
file_write 类型匹配器根本没实现。这里钉住:

  1. rules.yaml 里每条规则的 kind 匹配器都认识 (不认识 = 规则静默失效)
  2. 每条规则该拦的拦、不该拦的放 (尤其是正常的技能 patch)
  3. 插件入口 pre_tool_call 返回 hermes 认的 block 格式
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from matchers import match_rules  # noqa: E402

RULES = yaml.safe_load((HERE / "rules.yaml").read_text(encoding="utf-8"))
KNOWN_KINDS = {"shell_command", "file_read", "file_write", "network_request", "tool_name", "skill_op"}


def _hit(tool: str, args: dict) -> str | None:
    rule = match_rules(tool, args, RULES)
    return rule["id"] if rule else None


def test_每条规则的类型匹配器都认识():
    unknown = [(r["id"], r.get("kind")) for r in RULES["rules"] if r.get("kind") not in KNOWN_KINDS]
    assert not unknown, f"这些规则的 kind 匹配器不认识, 装上也不生效: {unknown}"


# ---- R1 ----

@pytest.mark.parametrize("cmd", [
    "rm -rf /", "rm -rf / --no-preserve-root", "rm -rf --no-preserve-root /", "sudo rm -rf /*",
    "rm -rf ~", "rm -fr ~/", "cd /tmp && rm -rf $HOME", "import os; os.system('rm -rf /')",
    "mkfs.ext4 /dev/sda1",
])
def test_R1_拦根目录和家目录删除(cmd):
    assert _hit("execute_code", {"code": cmd}) == "no-recursive-root-delete"


@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/build", "rm -rf ~/Downloads/old", "rm -rf ./dist", "rm -rf $HOME/.cache/x",
    "git commit -m 'never run rm -rf /tmp'", "echo mkfs",
])
def test_R1_放过限定目录的清理(cmd):
    assert _hit("execute_code", {"code": cmd}) is None


# ---- R6 ----

def test_R6_批量格式删_catfish_技能():
    args = {"operations": [{"action": "delete", "name": "catfish-email"}]}
    assert _hit("skill_manage", args) == "no-delete-catfish-skill"


def test_R6_老平铺格式和带分类名():
    assert _hit("skill_manage", {"action": "delete", "name": "catfish-email"}) == "no-delete-catfish-skill"
    assert _hit("skill_manage", {"action": "delete", "name": "productivity/catfish-email"}) == "no-delete-catfish-skill"


def test_R6_放过对_catfish_技能的_patch_和删别的技能():
    patch = {"operations": [{"action": "patch", "name": "catfish-email",
                             "old_string": "a", "new_string": "delete 之前先确认"}]}
    assert _hit("skill_manage", patch) is None
    assert _hit("skill_manage", {"operations": [{"action": "delete", "name": "my-notes"}]}) is None


# ---- R7 ----

@pytest.mark.parametrize("text", [
    "登录时 password: Hunter2024!", "api_key = abcdEFGH1234ijklMNOP5678",
    "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefg'", "key sk-proj-AbCdEf0123456789xyzXYZ",
])
def test_R7_拦明文凭据(text):
    for op in ({"action": "create", "name": "x", "content": text},
               {"action": "patch", "name": "x", "old_string": "a", "new_string": text},
               {"action": "write_file", "name": "x", "file_path": "references/a.md", "file_content": text}):
        assert _hit("skill_manage", {"operations": [op]}) == "skill-create-no-secrets", op


@pytest.mark.parametrize("text", [
    "password: <员工输入>", "password: ${PASSWORD}", "密码：员工每次输入", "api_key: your-key-here",
    "Authorization: Bearer <TOKEN>", 'Authorization: "Bearer sk-xxxxxxxxxxxxxxxxxxxx"',
    "收件人 zhang.san@example.com", "update 待办之前先读一遍",
])
def test_R7_放过占位符和普通正文(text):
    assert _hit("skill_manage", {"operations": [{"action": "create", "name": "x", "content": text}]}) is None


def test_R7_不看被替换掉的旧内容():
    op = {"action": "patch", "name": "x", "old_string": "password: Hunter2024!", "new_string": "password: <员工输入>"}
    assert _hit("skill_manage", {"operations": [op]}) is None


# ---- R9 ----

@pytest.mark.parametrize("tool,args", [
    ("write_file", {"path": "~/.hermes/SOUL.md", "content": "x"}),
    ("patch", {"path": str(Path.home() / ".hermes/USER.md"), "old_string": "a", "new_string": "b"}),
    ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: ~/.hermes/config.yaml\n@@\n-a\n+b\n"}),
])
def test_R9_拦改_hermes_控制文件(tool, args):
    assert _hit(tool, args) == "no-touch-hermes-control-files"


def test_R9_放过别的文件和读():
    assert _hit("write_file", {"path": "~/.hermes/memories/notes.md", "content": "x"}) is None
    assert _hit("read_file", {"path": "~/.hermes/config.yaml"}) is None


# ---- tool_name + deny_args_pattern (原来被忽略的那个分支) ----

def test_tool_name_带参数条件时只在参数命中才拦():
    rules = {"rules": [{"id": "r", "kind": "tool_name", "match": ["skill_manage"],
                        "deny_args_pattern": ['"action": "delete"']}]}
    assert match_rules("skill_manage", {"action": "patch", "name": "a"}, rules) is None
    assert match_rules("skill_manage", {"action": "delete", "name": "a"}, rules)["id"] == "r"


# ---- 插件入口 ----

def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "catfish_policy_under_test", HERE / "__init__.py", submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pre_tool_call_返回_hermes_认的格式(monkeypatch):
    monkeypatch.delenv("CATFISH_POLICY_DISABLED", raising=False)
    plugin = _load_plugin()
    out = plugin.pre_tool_call("skill_manage", {"operations": [{"action": "delete", "name": "catfish-email"}]})
    assert out["action"] == "block" and "no-delete-catfish-skill" in out["message"]
    assert plugin.pre_tool_call("skill_manage", {"operations": [{"action": "patch", "name": "a",
                                                                 "old_string": "x", "new_string": "y"}]}) is None
    monkeypatch.setenv("CATFISH_POLICY_DISABLED", "1")
    assert plugin.pre_tool_call("execute_code", {"code": "rm -rf /"}) is None
