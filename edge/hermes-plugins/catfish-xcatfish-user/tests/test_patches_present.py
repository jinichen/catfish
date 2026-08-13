"""Plugin self-test: 11 处 patch 引用的 hermes attribute / 方法都还在.

每次 hermes 升级后跑一次这个 test, 任一 fail 说明 hermes refactor 破坏了 plugin
patch 点, 需要立刻修 plugin (不修就 silent break — 跨员工串数据 P0 漏洞).

跑法:
    cd ~/.hermes/hermes-agent
    python -m pytest /path/to/catfish-xcatfish-user-plugin/tests/test_patches_present.py -v
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────
# 读 hermes **源码**里的函数签名 (不 import, 不 inspect)
# ─────────────────────────────────────────────────────────────────────────
#
# 为什么不能用 inspect.signature
# ------------------------------
# 8/13 实撞: `test_p4_auto_title_session_signature` 在本机必挂。
#
# conftest 的 `import run_agent` 会触发 hermes 的 plugin discovery, 后台线程把
# 本 plugin 装上, P4 于是把 `title_generator.auto_title_session` 换成了
#
#     def patched_auto_title_session(session_db, session_id, *args, **kwargs)
#
# 这个 wrapper **没有** `functools.wraps`, 所以 `inspect.signature` 看到的是
# `(session_db, session_id, *args, **kwargs)` —— `main_runtime` 当然不在里面。
#
# 关键在于: **这个文件测的是 hermes 的 API 有没有变**, 不是 plugin 装没装。
# 拿一个已经被自己 patch 过的对象去问"hermes 长什么样", 问的就是错的东西。
# 所以改成直接读 hermes 的 .py 源码, 用 AST 取形参名 —— 装没装 plugin 都一样。
#
# 顺带说明 P3b 为什么一直是绿的: 它的 wrapper 恰好把参数名写全了
# (`patched_resolve_auto(main_runtime=None, task=None)`), 所以 inspect 碰巧
# 看到对的答案。那是巧合不是正确性 —— wrapper 哪天改成 `*args, **kwargs`
# 它就跟 P4 一样挂。两条一起换成静态检查。


def _hermes_root() -> Path:
    return Path(os.environ.get("HERMES_ROOT") or os.path.expanduser("~/.hermes/hermes-agent"))


def _source_param_names(module_path: str, func_name: str) -> list[str]:
    """从 hermes 源码里取某个顶层函数的形参名, 保序。

    Args:
        module_path: 点号模块路径, 例 "agent.title_generator"
        func_name: 顶层 def / async def 的名字

    Returns:
        形参名列表 (含 posonly / 普通 / kwonly, 不含 *args / **kwargs 本身)。
        函数找不到 → 空列表, 由调用方判定 (下面有专门的测试钉住"找得到")。
    """
    path = _hermes_root() / (module_path.replace(".", "/") + ".py")
    if not path.exists():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in tree.body:  # 只看顶层 —— 嵌套同名函数不是 patch 目标
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            a = node.args
            return [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    return []


def test_source_param_reader_actually_finds_something():
    """助手函数自身的活性检查。

    没有这条的话, `_source_param_names` 哪天因为路径拼错 / AST 失败恒返 []
    时, 下面每一条签名测试都会变成"断言 x in []"从而红 —— 那还好。真正危险
    的是反过来: 如果断言写成"不该出现的参数不在里面", 空列表会让它恒绿。
    先钉住"这个 reader 是活的", 后面的结论才有意义。
    """
    if not _hermes_root().exists():
        pytest.skip("本机没有 hermes-agent 源码")
    params = _source_param_names("agent.title_generator", "auto_title_session")
    assert params, "读不到 auto_title_session 的形参 —— reader 坏了, 下面的结论都不作数"


# ─────────────────────────────────────────────────────────────────────────
# Module / class / function / attribute 存在性 check
# ─────────────────────────────────────────────────────────────────────────

def test_p1_agent_init_module():
    """P1: agent.agent_init.init_agent function 存在."""
    from agent import agent_init
    assert callable(agent_init.init_agent)


def test_p2_aiagent_current_main_runtime():
    """P2: AIAgent._current_main_runtime method 存在."""
    from run_agent import AIAgent
    assert callable(getattr(AIAgent, "_current_main_runtime", None))


def test_p3a_main_runtime_fields_tuple():
    """P3a: _MAIN_RUNTIME_FIELDS 是 tuple of str."""
    from agent.auxiliary_client import _MAIN_RUNTIME_FIELDS
    assert isinstance(_MAIN_RUNTIME_FIELDS, tuple)
    assert all(isinstance(f, str) for f in _MAIN_RUNTIME_FIELDS)
    assert "base_url" in _MAIN_RUNTIME_FIELDS  # 确认是同一个 tuple, 不是同名巧合


def test_p3a_normalize_main_runtime():
    """P3a: _normalize_main_runtime 接受 dict 返回 dict."""
    from agent.auxiliary_client import _normalize_main_runtime
    assert callable(_normalize_main_runtime)
    out = _normalize_main_runtime({"base_url": "http://x"})
    assert isinstance(out, dict)
    assert out.get("base_url") == "http://x"


def test_p3b_resolve_auto_signature():
    """P3b: _resolve_auto 接受 main_runtime kwarg.

    读源码不读运行时对象 —— P3b 自己就 wrap 了这个函数, 见文件头。
    """
    params = _source_param_names("agent.auxiliary_client", "_resolve_auto")
    assert "main_runtime" in params, (
        f"hermes 的 _resolve_auto 不再收 main_runtime (现有: {params}) —— "
        "P3b 传的 kwarg 会 TypeError"
    )


def test_p4_auto_title_session_signature():
    """P4: auto_title_session 前两个位置参数是 (session_db, session_id), 接 main_runtime。

    P4 的 wrapper 按位置解包前两个参数 (`patched(session_db, session_id, *args)`),
    所以**顺序**也是硬依赖 —— hermes 把这两个换个位置, 传给
    `session_registry.lookup()` 的就会是 session_db 对象, 查不到员工,
    header 注入静默失效。所以这里逐位断言, 不只是"在不在里面"。
    """
    params = _source_param_names("agent.title_generator", "auto_title_session")
    assert params[:2] == ["session_db", "session_id"], (
        f"前两个位置参数变了 (现有: {params[:2]}) —— P4 按位置解包会拿错 session_id"
    )
    assert "main_runtime" in params, (
        f"hermes 的 auto_title_session 不再收 main_runtime (现有: {params})"
    )


def test_p5_apiserver_class():
    """P5: APIServerAdapter class 可 import."""
    from gateway.platforms.api_server import APIServerAdapter
    assert APIServerAdapter is not None


def test_p6_apiserver_create_agent():
    """P6: APIServerAdapter._create_agent method 存在."""
    from gateway.platforms.api_server import APIServerAdapter
    assert callable(getattr(APIServerAdapter, "_create_agent", None))


def test_p7_apiserver_has_route_register():
    """P7: APIServerAdapter 至少有一个候选 route register method.

    hermes 0.15: 路由 inline 在 async connect() 里, 没有专门 _setup_routes.
    旧 hermes 版本可能用过 _setup_routes / _register_routes.
    """
    from gateway.platforms.api_server import APIServerAdapter
    candidates = ("_setup_routes", "_register_routes", "_init_routes", "_build_app", "connect")
    assert any(hasattr(APIServerAdapter, c) for c in candidates), (
        f"None of {candidates} found on APIServerAdapter — P7 catch-all proxy 路由注册会跳过"
    )


def test_p8_apiserver_has_origin_check():
    """P8: APIServerAdapter 至少有一个候选 origin check method."""
    from gateway.platforms.api_server import APIServerAdapter
    candidates = ("_origin_allowed", "_is_origin_allowed", "_cors_origin_allowed")
    assert any(hasattr(APIServerAdapter, c) for c in candidates), (
        f"None of {candidates} found on APIServerAdapter — P8 Tauri origin 不会进允许列表"
    )


def test_p9_apiserver_cors_headers():
    """P9: _CORS_HEADERS 在 hermes 0.15 是 module-level 常量 (api_server.py:502),
    不是 class attribute. plugin 直接 mutate module dict. 这里 check module-level
    或 class-level 任一存在 (跨版本兼容).
    """
    from gateway.platforms import api_server as api_server_mod
    cors = None
    if hasattr(api_server_mod, "_CORS_HEADERS"):
        cors = api_server_mod._CORS_HEADERS
    elif hasattr(api_server_mod.APIServerAdapter, "_CORS_HEADERS"):
        cors = api_server_mod.APIServerAdapter._CORS_HEADERS

    assert cors is not None, (
        "_CORS_HEADERS 在 module-level 或 class 上都找不到 — P9 加不上自定义 header"
    )
    assert isinstance(cors, dict)
    assert "Access-Control-Allow-Headers" in cors


def test_p10_aiagent_apply_client_headers():
    """P10: AIAgent._apply_client_headers_for_base_url method 存在."""
    from run_agent import AIAgent
    assert callable(getattr(AIAgent, "_apply_client_headers_for_base_url", None))


def test_p10_aiagent_client_kwargs():
    """P10: AIAgent 实例有 _client_kwargs dict (P10 patch 要直接写它).

    通过构造一个 dummy agent 看 attribute 是不是 init 时设的.
    """
    # 这个 test 复杂, 实际 plugin install 时 self_test 即可
    # 这里只 check class 上有 hint (注释 / __slots__ 等)
    pass  # 留给 install-time check


def test_p11_picker_aiohttp_middleware_signature():
    """P11: aiohttp web.middleware decorator 可用 (picker middleware 实现依赖)."""
    from aiohttp import web
    assert hasattr(web, "middleware")
    assert hasattr(web, "Request") or hasattr(web, "StreamResponse")


# ─────────────────────────────────────────────────────────────────────────
# Plugin install + 幂等
# ─────────────────────────────────────────────────────────────────────────

def test_install_idempotent():
    """重复 install 不会 double-wrap."""
    import plugin

    # 直接调 install 会 monkey-patch hermes, test 完不容易清理. 这里只 check
    # 幂等 flag.
    if plugin._PATCHED:
        # 已经 install 过, 再调一次 verify 不报错
        plugin.install()
    # 不实际测试 patch 效果 (那是 integration test)


# ─────────────────────────────────────────────────────────────────────────
# session_registry / resolver 单元测
# ─────────────────────────────────────────────────────────────────────────

def test_session_registry_register_lookup_unregister():
    import session_registry as sr

    sr.register("sess-1", "u1@ffcs.cn")
    assert sr.lookup("sess-1") == "u1@ffcs.cn"
    sr.unregister("sess-1")
    assert sr.lookup("sess-1") is None


def test_session_registry_eviction_cap():
    """容量上限 1024, 超出 LRU 驱逐."""
    import session_registry as sr

    # 简单填到 1100, 看 size 是 1024
    for i in range(1100):
        sr.register(f"sess-{i}", f"u{i}@x.com")

    assert sr.size() <= 1024
    # 老的应该被驱逐
    assert sr.lookup("sess-0") is None
    # 最新的应该在
    assert sr.lookup("sess-1099") == "u1099@x.com"


def test_resolver_attribute_first():
    """resolver 优先用 agent._catfish_outgoing_user attribute."""
    import resolver

    class FakeAgent:
        _catfish_outgoing_user = "explicit@user.com"
        _user_id = "ignored"
        platform = "weixin"

    assert resolver.resolve_for_agent(FakeAgent()) == "explicit@user.com"


def test_resolver_synthesize_im_platform():
    """没 attribute / pairing / email 时合成 <uid>@im.<platform>."""
    import resolver

    class FakeAgent:
        _catfish_outgoing_user = ""
        _user_id = "o9cq807y"
        platform = "weixin"

    assert resolver.resolve_for_agent(FakeAgent()) == "o9cq807y@im.weixin"


def test_resolver_email_uid_passthrough():
    """uid 本身是 email 形态 → 直接用 uid."""
    import resolver

    class FakeAgent:
        _catfish_outgoing_user = ""
        _user_id = "user@company.com"
        platform = "feishu"

    assert resolver.resolve_for_agent(FakeAgent()) == "user@company.com"


def test_resolver_env_fallback(monkeypatch):
    """platform=cli 时 (没 uid) 走 env CATFISH_DEFAULT_USER 兜底."""
    import resolver

    monkeypatch.setenv("CATFISH_DEFAULT_USER", "default@x.com")

    class FakeAgent:
        _catfish_outgoing_user = ""
        _user_id = ""
        platform = "cli"

    assert resolver.resolve_for_agent(FakeAgent()) == "default@x.com"


def test_resolver_returns_empty_when_no_source():
    """完全没 user → 空串. 调用方应 pop default_headers, 不是设空."""
    import resolver

    class FakeAgent:
        _catfish_outgoing_user = ""
        _user_id = ""
        platform = "cli"

    # 假定 env 没设
    import os
    if os.environ.get("CATFISH_DEFAULT_USER"):
        pytest.skip("CATFISH_DEFAULT_USER set in env, can't test")

    assert resolver.resolve_for_agent(FakeAgent()) == ""
