"""11/17: 专家机器人 profile 加载插件时 HERMES_HOME = profiles/<id> (下面没有 hermes-agent),
静态检查原来按 <HERMES_HOME>/hermes-agent 找源码 → 全部补丁点「缺失」→ 插件拒绝加载。
现在按进程里已导入的 hermes 模块定位源码。要真 hermes 源码 (HERMES_ROOT / ~/.hermes/hermes-agent), 没有就跳过。"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("_test_plugin_verify", PLUGIN / "plugin_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_专家profile的HERMES_HOME下也找得到hermes源码(tmp_path, monkeypatch):
    spec = importlib.util.find_spec("run_agent")
    if spec is None or not spec.origin:
        pytest.skip("没有 hermes 源码在 sys.path 上")
    real_root = str(Path(spec.origin).resolve().parent)
    profile_home = tmp_path / "profiles" / "expert-x"
    profile_home.mkdir(parents=True)
    monkeypatch.delenv("HERMES_ROOT", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    mod = _load()
    assert mod._hermes_root() == real_root
    assert not (profile_home / "hermes-agent").exists()
    mod._verify_patch_targets()  # 原来这里抛「hermes refactor 破坏了 patch targets」
