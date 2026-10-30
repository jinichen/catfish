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


def test_全局补丁只归第一份插件(monkeypatch):
    """钩子 / 工具两份都注册 (按 profile 各挂各的), 改 class 的补丁只打一遍。"""
    a = _load_as("hermes_plugins.catfish_xcatfish_user.plugin_ctx_t2")
    b = _load_as("hermes_plugins.catfish_xcatfish_user__home_abc.plugin_ctx_t2")
    monkeypatch.delattr(a._SHARED, "patch_owner", raising=False)
    monkeypatch.delattr(a._SHARED, "patches_installed", raising=False)
    assert a.claim_process_patches("hermes_plugins.catfish_xcatfish_user") is True
    assert b.claim_process_patches("hermes_plugins.catfish_xcatfish_user__home_abc") is False
    # 同一份再问一次 (比如 hermes 重复调 register) 仍然是它的
    assert a.claim_process_patches("hermes_plugins.catfish_xcatfish_user") is True
    # 第二份的工具检查钩子看的是「进程里有没有一份装好」, 不是只看自己
    assert b.process_patches_installed() is False
    a.mark_process_patches_installed()
    assert b.process_patches_installed() is True


def test_register_非第一份不碰全局补丁():
    """__init__.register 里: 非第一份在进程级补丁之前 return, egress 也不挂。"""
    src = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    gate = src.index("if not _owns_patches:\n        logger.info(\"catfish-xcatfish-user: register ✓")
    assert gate < src.index("_mod._patch_asyncio_executor_for_contextvars()")
    assert gate < src.index("_mod._patch_p15_2_chat_approval_route()")
    assert gate < src.index("target=_delayed_install")
    assert src.index("if _owns_patches:") < src.index('_egress.install("hermes")')
    # 钩子 / 工具注册在门之前, 两份都会做
    assert src.index('ctx.register_hook("pre_tool_call"') < gate
    assert src.index("ctx.register_tool(") < gate
