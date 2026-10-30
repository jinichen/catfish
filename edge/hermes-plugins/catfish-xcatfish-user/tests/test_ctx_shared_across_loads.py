"""10/30: hermes 把插件加载两份 (主目录 + 专家机器人 profile), CV 必须是同一个对象。

不是同一个时, 后加载那份的补丁读不到 middleware set 的来源, 把
X-Catfish-Source 盖成 hermes:api_server (门户里聊天 / 邮件分诊全显示成「对话」)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_as(name: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / "plugin_ctx.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_两份插件共用同一组_contextvar():
    a = _load_as("hermes_plugins.catfish_xcatfish_user.plugin_ctx_t")
    b = _load_as("hermes_plugins.catfish_xcatfish_user__home_abc.plugin_ctx_t")
    assert a is not b
    assert a.CV_CF_SOURCE is b.CV_CF_SOURCE
    assert a.CV_CF_USER is b.CV_CF_USER
    assert a.CV_PICKER_MODEL is b.CV_PICKER_MODEL
    token = a.CV_CF_SOURCE.set("companion-chat")
    try:
        assert b.CV_CF_SOURCE.get() == "companion-chat"
    finally:
        a.CV_CF_SOURCE.reset(token)
