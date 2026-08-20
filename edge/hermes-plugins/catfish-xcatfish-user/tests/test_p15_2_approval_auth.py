"""P15.2 审批端点必须验 Bearer token。

# 守的是什么

`POST /v1/sessions/{sid}/approval` 是「execute_code 每次必须人工批准」这条红线的
**执行点** —— 打通它就等于替员工点了"同意"。

6/6 P15.2 落地到 8/20, 这条 endpoint 一直没有鉴权。原因不是忘了写, 是两件事撞在
一起:

  · catfish 这个 middleware 是**短路**的 —— 匹配到路径直接 return, 从不调
    handler(request)
  · hermes 的鉴权不是 middleware, 是每个 handler 自己第一行调 `_check_auth`
    (api_server.py 里 29 处, 全手写; 上游那 4 个 middleware 都不管鉴权)

于是请求在进 handler 之前就被截走, `_check_auth` 永远跑不到。

最难发现的地方在于它**看起来**是有鉴权的 —— 三个客户端都在老老实实发头:

    Companion  tauri_services.ts:172   headers["Authorization"] = hermesAuthHeader
    Rust 代理   http_proxy.rs:220       for (k, v) in &req.headers   ← 逐条透传
    冒烟测试    smoke-test.sh:198       -H "Authorization: Bearer $HERMES_API_KEY"

头一路送到底, 只是没有任何人验它。这是「**不会失败, 也不会生效**」那一族:
从任何一端看这条链都像有鉴权, 判据比真事宽了整整一格。

# 为什么测试要这么写

**必须真起 aiohttp 跑一遍。** 只读源码断言"文件里有 _check_auth 这几个字"是测不出
东西的 —— 8/20 之前的代码里也有 `_check_auth` 这个字符串(在别的 patch 里),
而且中间件短路这件事只有真发一个请求才看得见。

**每条拒绝用例都要同时断言 `resolve_gateway_approval` 没被调用。** 只断言状态码
是不够的: 一个"先 resolve 了再返 401"的实现照样能让状态码测试变绿, 而审批已经
被放行了。真正要守的是那次副作用有没有发生。

**fail-closed 的两条 (adapter 缺失 / `_check_auth` 改名) 必须单独测。** 这两条是
上游重构时最可能踩的, 而踩中的表现是"放行"还是"拒绝"完全取决于我们怎么写 —— 写
成放行的话, 这道门重新变回摆设, 而且没有任何人会发现。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import types
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

_DIR = Path(__file__).resolve().parent.parent
_GOOD_KEY = "test-api-server-key-0123456789abcdef"


# ─────────────────────────────────────────────────────────────────────
# 脚手架
# ─────────────────────────────────────────────────────────────────────
class _FakeAdapter:
    """照抄上游 `_check_auth` 的契约: 通过返 None, 拒绝返 Response(401)。

    契约本身由 test_上游check_auth的契约没变() 钉在真 hermes 树上 ——
    这里只是复刻, 不是真源。
    """

    def __init__(self, expected: str = _GOOD_KEY) -> None:
        self.expected = expected
        self.calls = 0

    def _check_auth(self, request):
        self.calls += 1
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == self.expected:
            return None
        return web.json_response(
            {"error": {"message": "Invalid gateway API key (API_SERVER_KEY)",
                       "code": "gateway_auth_failed"}},
            status=401,
        )


def _build_middleware(resolve_calls: list):
    """装 P15.2, 拿到中间件。`tools.approval` 在测试环境不存在, 用桩顶上。"""
    def _resolve(session_key, choice, *a, **kw):
        resolve_calls.append((session_key, choice, a, kw))
        return 1

    approval_mod = types.ModuleType("tools.approval")
    approval_mod.resolve_gateway_approval = _resolve
    tools_pkg = sys.modules.get("tools") or types.ModuleType("tools")

    saved = {k: sys.modules.get(k) for k in ("tools", "tools.approval")}
    sys.modules["tools"] = tools_pkg
    sys.modules["tools.approval"] = approval_mod
    try:
        import plugin_approval

        plugin_approval._chat_approval_middleware = None
        plugin_approval._patch_p15_2_chat_approval_route()
        mw = plugin_approval._chat_approval_middleware
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    assert mw is not None, "P15.2 没装上 —— 后面的断言全都没有意义"
    return mw


def _post(mw, path: str, *, headers=None, adapter=..., body: str = '{"choice": "deny"}'):
    """真起一个 aiohttp app 发请求, 返 (status, text, 是否落到了下游 handler)。"""
    downstream = {"hit": False}

    async def _tail(request):
        downstream["hit"] = True
        return web.json_response({"downstream": True})

    async def _run():
        app = web.Application(middlewares=[mw])
        app.router.add_route("*", "/{tail:.*}", _tail)
        if adapter is not ...:
            app["api_server_adapter"] = adapter
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post(path, headers=headers or {}, data=body)
            return resp.status, await resp.text()
        finally:
            await client.close()

    status, text = asyncio.run(_run())
    return status, text, downstream["hit"]


# ─────────────────────────────────────────────────────────────────────
# 1. 拒绝路径 —— 状态码 **和** 副作用都要断言
# ─────────────────────────────────────────────────────────────────────
def test_没有token_必须401且不解审批():
    calls: list = []
    mw = _build_middleware(calls)
    adapter = _FakeAdapter()

    status, _text, hit_downstream = _post(
        mw, "/v1/sessions/sid-abc/approval", adapter=adapter
    )

    assert status == 401, f"不带 token 竟然不是 401 (拿到 {status}) —— 门是开的"
    assert calls == [], (
        "已经 resolve 了审批才返回 401 —— 状态码好看, 但批准**已经生效**了。"
        f"resolve_gateway_approval 被调用: {calls}"
    )
    assert not hit_downstream, "不该落到下游 handler"
    assert adapter.calls == 1, "根本没调 _check_auth"


def test_错token_必须401且不解审批():
    calls: list = []
    mw = _build_middleware(calls)
    adapter = _FakeAdapter()

    status, _t, _h = _post(
        mw, "/v1/sessions/sid-abc/approval",
        headers={"Authorization": "Bearer wrong-key"}, adapter=adapter,
    )

    assert status == 401, f"错 token 竟然不是 401 (拿到 {status})"
    assert calls == [], f"错 token 却解了审批: {calls}"


def test_空Bearer_必须401():
    """`Bearer ` 后面什么都没有 —— 客户端配错 key 时的真实形状。"""
    calls: list = []
    mw = _build_middleware(calls)

    status, _t, _h = _post(
        mw, "/v1/sessions/sid-abc/approval",
        headers={"Authorization": "Bearer "}, adapter=_FakeAdapter(),
    )

    assert status == 401
    assert calls == []


# ─────────────────────────────────────────────────────────────────────
# 2. 放行路径 —— 别把门焊死了
# ─────────────────────────────────────────────────────────────────────
def test_对的token_正常解审批():
    """这条是防"修过头": 补鉴权不能把审批按钮弄死。

    真实环境里这条一定是通的 —— ~/.hermes/.env 的 API_SERVER_KEY 和
    ~/.catfish/companion.yaml 的 hermes_api.key 是同一个值 (8/20 比对过 sha256),
    Companion 发的就是它。
    """
    calls: list = []
    mw = _build_middleware(calls)

    status, text, _h = _post(
        mw, "/v1/sessions/sid-abc/approval",
        headers={"Authorization": f"Bearer {_GOOD_KEY}"}, adapter=_FakeAdapter(),
    )

    assert status == 200, f"带对 token 反而不通 (拿到 {status}: {text}) —— 审批按钮被焊死了"
    assert calls == [("sid-abc", "deny", (), {})], (
        f"resolve 没按预期调用: {calls}"
    )
    assert '"resolved"' in text and '"choice"' in text


def test_非审批路径不受影响():
    """中间件只该管自己那条路径, 别的请求原样放过 (也不该去碰鉴权)。"""
    calls: list = []
    mw = _build_middleware(calls)
    adapter = _FakeAdapter()

    status, _t, hit_downstream = _post(mw, "/v1/chat/completions", adapter=adapter)

    assert hit_downstream, "普通请求没落到下游 handler —— 中间件拦错了东西"
    assert status == 200
    assert adapter.calls == 0, (
        "对不归自己管的请求也调了 _check_auth —— 会和上游 handler 里那次重复"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. fail-closed —— 上游重构时最可能踩的两条
# ─────────────────────────────────────────────────────────────────────
def test_拿不到adapter时_拒绝而不是放行():
    """app 上没有 api_server_adapter (上游改了那行赋值) → 必须拒绝。

    放行的话这道门就重新变回摆设了, 而且不会有任何人发现。
    """
    calls: list = []
    mw = _build_middleware(calls)

    status, _t, _h = _post(
        mw, "/v1/sessions/sid-abc/approval",
        headers={"Authorization": f"Bearer {_GOOD_KEY}"}, adapter=...,
    )

    assert status == 503, f"拿不到 adapter 时没有 fail-closed (拿到 {status})"
    assert calls == [], f"拿不到 adapter 却仍然解了审批: {calls}"


def test_check_auth被改名时_拒绝而不是放行():
    """adapter 在, 但 `_check_auth` 不见了 (上游改名) → 必须拒绝。"""
    calls: list = []
    mw = _build_middleware(calls)

    class _Renamed:  # 没有 _check_auth
        pass

    status, _t, _h = _post(
        mw, "/v1/sessions/sid-abc/approval",
        headers={"Authorization": f"Bearer {_GOOD_KEY}"}, adapter=_Renamed(),
    )

    assert status == 503, f"_check_auth 改名后没有 fail-closed (拿到 {status})"
    assert calls == [], f"_check_auth 都没了还解审批: {calls}"


# ─────────────────────────────────────────────────────────────────────
# 4. 源码级 —— 运行时测不出的"写法退化"
# ─────────────────────────────────────────────────────────────────────
def test_鉴权必须在读body之前():
    """未鉴权的请求不该让我们花 IO 去读它的 body。

    运行时测不出这个顺序 (两种顺序的状态码一模一样), 只能看源码。

    # 这条判据自己踩过一次坑 (8/20)

    第一版写的是 `src.find("_require_api_auth(request)") < src.find("await
    request.json()")` —— 用字符串位置比。变异测试直接把它戳穿了: `find` 命中的是
    文件顶部那行**函数定义** `def _require_api_auth(request):`, 它当然在
    request.json() 前面, 所以无论调用点挪到哪, 这条断言都是真的。

    把鉴权真的挪到读 body 之后, 测试**照样绿** —— 判据比真事宽了一格,
    跟它要守的那个 bug 是同一种病。

    改成 ast: 只在 `chat_approval_middleware` 函数体内找, 且只认**调用**不认定义。
    """
    import ast

    src = (_DIR / "plugin_approval.py").read_text(encoding="utf-8")
    fn = next(
        (n for n in ast.walk(ast.parse(src))
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "chat_approval_middleware"),
        None,
    )
    assert fn is not None, "找不到 chat_approval_middleware —— 结构变了, 这条判据要重写"

    auth = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_require_api_auth"
    ]
    read_body = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "json"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "request"
    ]
    assert auth, "middleware 里没有调用 _require_api_auth —— 鉴权被删了"
    assert read_body, "middleware 里找不到 request.json() —— 结构变了, 这条判据要重写"
    assert max(auth) < min(read_body), (
        f"鉴权 (行 {auth}) 跑在读 body (行 {read_body}) 之后了 —— "
        "未鉴权的请求不该让我们去读它的 body"
    )


def test_装载期钉住了check_auth():
    """plugin_verify 必须把 `_check_auth` 列进 APIServer 方法清单。

    没有它, 上游改名的表现是"员工点不动审批按钮", 而且要查很久才会想到是
    改名引起的。有它, 插件装载期就炸, 报出确切的原因。
    """
    src = (_DIR / "plugin_verify.py").read_text(encoding="utf-8")
    m = re.search(r"_APISERVER_METHOD_TARGETS\s*=\s*\[(.*?)\]", src, re.S)
    assert m, "_APISERVER_METHOD_TARGETS 不见了 —— plugin_verify 结构变了"
    assert '"_check_auth"' in m.group(1), (
        "_check_auth 没在 _APISERVER_METHOD_TARGETS 里 —— "
        "上游改名不会 fail-loud, 只会让审批按钮静默失效"
    )


def test_resolve_all_已经删干净():
    """零调用方的批量批准开关 (8/20 删)。

    留着的话, 一个没人用的参数在安全端点上放大权限 —— 一次请求解掉
    session 里**所有**挂起的审批。
    """
    src = (_DIR / "plugin_approval.py").read_text(encoding="utf-8")
    live = [
        f"{i}: {ln.strip()[:70]}"
        for i, ln in enumerate(src.splitlines(), 1)
        if "resolve_all" in ln and not ln.strip().startswith("#")
    ]
    assert not live, "resolve_all 还有活引用:\n" + "\n".join(live)


# ─────────────────────────────────────────────────────────────────────
# 5. 上游契约 —— 我们新依赖的东西, 照 P25/P36 的规矩钉住
# ─────────────────────────────────────────────────────────────────────
_HERMES_ROOT = Path(
    os.environ.get("HERMES_ROOT", Path.home() / ".hermes" / "hermes-agent")
)
_API_SERVER = _HERMES_ROOT / "gateway" / "platforms" / "api_server.py"

_needs_hermes = pytest.mark.skipif(
    not _API_SERVER.exists(),
    reason=f"没有 hermes 树可对照 ({_API_SERVER}) —— CI/沙箱里跳过",
)


@_needs_hermes
def test_上游把adapter挂在app上():
    """`request.app["api_server_adapter"]` 是我们拿实例的唯一路子。

    上游拿掉这行 → 运行时 fail-closed (审批按钮失效)。钉在这里, 让升级审计
    先看见。
    """
    src = _API_SERVER.read_text(encoding="utf-8", errors="replace")
    assert re.search(
        r"""_app\[\s*['"]api_server_adapter['"]\s*\]\s*=\s*self""", src
    ), (
        "上游不再把 adapter 挂到 app 上 —— P15.2 的鉴权拿不到实例, 会 fail-closed "
        "(审批按钮失效). 见 plugin_approval._require_api_auth。"
    )


@_needs_hermes
def test_上游check_auth的契约没变():
    """`_check_auth` 必须仍是「通过返 None / 拒绝返 Response」。

    这条是**判据方向**的问题, 不是有没有的问题: 如果上游改成返 True/False,
    我们的 `if auth_err is not None` 会把 False 当成"拒绝对象"直接返出去 ——
    结果是**每一次审批都被拒**, 而且 401 里的 body 会是个 `False`。
    形状变了但代码不报错, 正是最难查的那种。
    """
    src = _API_SERVER.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"def _check_auth\(self.*?\n(.*?)(?=\n    @|\n    def )", src, re.S)
    assert m, "找不到 _check_auth 函数体 —— 结构变了, 重新评估这条依赖"
    body = m.group(1)
    assert re.search(r"return None\b", body), (
        "_check_auth 不再返 None 表示通过 —— 契约变了, "
        "plugin_approval._require_api_auth 的 `is not None` 判据要跟着改。"
    )
    assert "status=401" in body, (
        "_check_auth 不再返 401 Response 表示拒绝 —— 契约变了, 去读实现。"
    )
    assert "Authorization" in body and "Bearer" in body, (
        "_check_auth 不再校验 Bearer 头 —— 鉴权方式变了, 重新确认 Companion 发的东西还对不对。"
    )
