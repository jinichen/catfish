"""全测试默认隔离员工本机数据 (10/17)。

以前每个测试文件自己记得 setenv CATFISH_HOME, 漏了就写进真 ~/.catfish (10/17 工作流测试把草稿
写进了真的 workflow_drafts/, 一次探测往真任务库里建了条任务)。这里默认就指到临时目录;
测试自己再 setenv 会覆盖这个默认。

登录凭据也一样: 这几个模块在 import 时就把 `Path.home()/.catfish/oauth/id_token` 算成常量, 测试走到要调模型的
分支就拿真凭据去打本机 gateway 的真模型 (memory_dedupe 两条测试因此时过时不过 —— 模型每次答得不一样)。
这里把常量默认指到不存在的临时路径: 要模型的分支走离线兜底, 结果固定; 真要凭据的测试自己打桩。
新模块要是也在 import 时算凭据路径, 加进 _TOKEN_MODULES (test_conftest_guard 会提醒)。
"""
from __future__ import annotations

import importlib

import pytest

_TOKEN_MODULES = ("browser_locate", "recognize_captcha", "skill_publish", "wiki_publish")


@pytest.fixture(autouse=True)
def _isolate_catfish_home(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("catfish-home")
    monkeypatch.setenv("CATFISH_HOME", str(base))
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(base / "task_library.db"))
    fake_token = base / "no-login" / "id_token"
    for name in _TOKEN_MODULES:
        mod = importlib.import_module(f"catfish_tool_bridge.{name}")
        monkeypatch.setattr(mod, "OAUTH_ID_TOKEN_PATH", fake_token)
