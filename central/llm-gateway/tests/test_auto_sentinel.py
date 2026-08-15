"""`catfish-auto` sentinel 解析 —— 两个端点必须给出同一个答案。

# 这个文件钉的是什么

8/13 实撞: sentinel 解析原来只写在 `chat_completions` 里, 于是

    POST /v1/chat/completions  model=catfish-auto  → 200
    GET  /v1/models/catfish-auto                   → 404

hermes 的 `agent/model_metadata.py:_query_local_context_length_uncached` 正是靠
`GET /v1/models/{model}` 拿 context_length。404 → 落
`DEFAULT_FALLBACK_CONTEXT = 256_000`, 而且**fallback 不写缓存**, 所以每次
`_create_agent` 重探一遍。实测佐证: `~/.hermes/context_length_cache.yaml` 里
每个真 model 名都有条目, 唯独 `catfish-auto` 没有。

失效方式是静默的 —— 没有报错, 只是上下文窗口用了个错的数。所以每条不变式
都得有测试。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from catfish_gateway import app as app_module
from catfish_gateway.app import AUTO_MODEL_SENTINEL, _resolve_auto_sentinel


@pytest.fixture
def chat_default(monkeypatch):
    """把 roles.resolve_or_none 换成可控的桩。返回一个 setter。"""
    def _set(value):
        from catfish_gateway import roles as roles_module
        monkeypatch.setattr(
            roles_module, "resolve_or_none",
            lambda role: value if role == "chat_default" else None,
        )
    return _set


# ── 解析行为 ────────────────────────────────────────────────────

def test_sentinel_resolves_to_chat_default(chat_default):
    chat_default("catfish-public-deepseek-flash")
    assert _resolve_auto_sentinel(AUTO_MODEL_SENTINEL) == "catfish-public-deepseek-flash"


def test_sentinel_match_is_case_insensitive(chat_default):
    """hermes config.yaml 是手写的, 大小写不该成为"为什么我的没生效"。"""
    chat_default("catfish-public-deepseek-flash")
    for spelling in ("Catfish-Auto", "CATFISH-AUTO", "catfish-AUTO"):
        assert _resolve_auto_sentinel(spelling) == "catfish-public-deepseek-flash"


def test_real_model_names_pass_through_untouched(chat_default):
    """非 sentinel 一律原样返回 —— 连 roles.yaml 都不该去读。"""
    chat_default("catfish-public-deepseek-flash")
    for name in ("catfish-private-vision", "gpt-5.6-luna", "catfish-public-gemini-pro"):
        assert _resolve_auto_sentinel(name) == name


def test_only_exact_name_matches_not_substring(chat_default):
    """`catfish-auto-v2` 是个普通名字, 不该被当成 sentinel 吞掉。

    用 `in` / `startswith` 写这段判据就会红 —— 那种写法会把客户将来自己起的
    `catfish-auto-*` 模型名全部劫持成 chat_default, 而且毫无提示。
    """
    chat_default("catfish-public-deepseek-flash")
    for name in ("catfish-auto-v2", "my-catfish-auto", "catfish-automatic"):
        assert _resolve_auto_sentinel(name) == name


def test_unconfigured_chat_default_is_500_not_404(chat_default):
    """roles.yaml 没配 = 部署错误, 不是请求错误。

    返 404 会让 IT 以为"是模型名写错了"去改客户端, 而真正要改的是 roles.yaml。
    """
    chat_default(None)
    with pytest.raises(HTTPException) as exc:
        _resolve_auto_sentinel(AUTO_MODEL_SENTINEL)
    assert exc.value.status_code == 500
    assert "chat_default" in str(exc.value.detail)


def test_error_detail_names_the_file_to_edit(chat_default):
    """报错要说"改哪个文件的哪一行", 不是"model not found"。"""
    chat_default(None)
    with pytest.raises(HTTPException) as exc:
        _resolve_auto_sentinel(AUTO_MODEL_SENTINEL)
    assert "roles.yaml" in str(exc.value.detail)



def _handler_source(name: str) -> str:
    """按函数名在 gateway 包里找 handler 的源码。

    拆分之后路由分散在 app.py / misc_routes.py / *_router.py, 而且不少是挂在
    register_*(app) 里的局部函数, `getattr(module, name)` 取不到。用 AST 在包内
    逐文件找同名 FunctionDef, 找到唯一一个就返它的源码。

    "唯一一个"顺带钉住了别的事: 同名 handler 出现两份 = 有人复制了一个端点。
    """
    import ast as _ast

    def _is_route(n) -> bool:
        """带 @app.get/@app.post 之类装饰器的才算 handler。

        第一版只按函数名找, 结果 `get_model` 撞上了 config.py 里
        `Config.get_model` 这个同名方法 —— 判据比真事宽。加这一条收窄。
        """
        for d in getattr(n, "decorator_list", []):
            if (isinstance(d, _ast.Call) and isinstance(d.func, _ast.Attribute)
                    and isinstance(d.func.value, _ast.Name) and d.func.value.id == "app"):
                return True
        return False

    pkg = Path(app_module.__file__).parent
    hits = []
    for f in sorted(pkg.glob("*.py")):
        fsrc = f.read_text(encoding="utf-8")
        for n in _ast.walk(_ast.parse(fsrc)):
            if (isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                    and n.name == name and _is_route(n)):
                hits.append((f.name, _ast.get_source_segment(fsrc, n) or ""))
    assert hits, f"包里找不到 handler {name}() —— 改名了还是删了?"
    assert len(hits) == 1, f"{name}() 有 {len(hits)} 份定义: {[h[0] for h in hits]}"
    return hits[0][1]


def _handler_code(name: str) -> str:
    """handler 的**可执行语句**源码 —— 剥掉 docstring。

    8/15 发现原判据是假的: 断言 `"_resolve_auto_sentinel" in src` 在整个函数
    源码里找子串, 而 get_model 的 docstring 里正好写着这个名字
    ("见 `_resolve_auto_sentinel` 的长注释")。实测把
    `config.get_model(_resolve_auto_sentinel(model_id))` 改成
    `config.get_model(model_id)`, 这条**照样绿** —— 注释写得越清楚, 它越抓不到。

    今天同一形状踩过好几次 (test_picker_reader_single_source 那条也是靠跳
    docstring 才修对的)。这里索性把 docstring 剥掉再判。
    """
    import ast as _ast
    src = _handler_source(name)
    node = _ast.parse(src).body[0]
    stmts = [
        s for s in node.body
        if not (isinstance(s, _ast.Expr) and isinstance(s.value, _ast.Constant)
                and isinstance(s.value.value, str))
    ]
    return "\n".join(_ast.get_source_segment(src, s) or "" for s in stmts)


# ── 两个端点必须共用同一条路径 ──────────────────────────────────

def test_get_model_endpoint_resolves_the_sentinel():
    """GET /v1/models/{id} 的实现里必须出现 `_resolve_auto_sentinel`。

    这是回归本身。用源码检查而不是发请求, 是因为发请求要拉起整个 app +
    auth 依赖, 而这里要钉的东西很窄: **这个 handler 有没有走那条共用路径**。
    """
    # 8/15: GET /v1/models/{id} 搬到了 misc_routes.py, 而且是挂在
    # register_misc_routes() 内部的局部函数 —— 模块上取不到属性。
    # 判据从"取 app.get_model 的源码"改成"在宿主模块的源码里定位这个 handler"。
    # 要守的东西没变: **这个 handler 有没有走 _resolve_auto_sentinel 那条共用路径**。
    src = _handler_code("get_model")
    assert "_resolve_auto_sentinel" in src, (
        "GET /v1/models/{id} 没解析 sentinel —— hermes 会拿不到 context_length, "
        "退回 DEFAULT_FALLBACK_CONTEXT 且永不缓存 (每次 _create_agent 重探)"
    )


def test_chat_completions_endpoint_resolves_the_sentinel():
    src = inspect.getsource(app_module.chat_completions)
    assert "_resolve_auto_sentinel" in src


def test_sentinel_literal_appears_only_in_the_constant():
    """字面量 "catfish-auto" 在代码里只能出现一次 (常量定义处)。

    第二次出现 = 又一个"只改了一处"的机会。注释里出现不算, 所以这里只扫
    非注释行。
    """
    # 8/15: 判据从"只扫 app.py"改成"扫常量所在的整个包"。
    #
    # 那天 AUTO_MODEL_SENTINEL 跟 _resolve_auto_sentinel 一起搬去了
    # llm_params.py, app.py 里就一次都不出现了 —— 老断言 len(hits)==1 反而红,
    # 而它要守的规矩 (字面量只能有一处, 别再散落) 一个字没变。
    #
    # 扫整个 src/catfish_gateway/ 比原来只扫一个文件**更严**: 常量搬到哪都行,
    # 但全包范围内仍然只准出现一次。
    pkg = Path(app_module.__file__).parent
    hits = []
    for f in sorted(pkg.glob("*.py")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.lstrip().startswith(("#", "#:")) and '"catfish-auto"' in ln:
                hits.append((f.name, ln.strip()))
    assert len(hits) == 1, (
        f"sentinel 字面量散落在多处, 该用 AUTO_MODEL_SENTINEL: {hits}"
    )
    # 唯一那处必须**就是常量定义**, 不是碰巧写在别处的字面量。
    _where, _line = hits[0]
    assert _line.startswith("AUTO_MODEL_SENTINEL ="), f"{_where}: {_line}"


# ── 跨仓一致性 ──────────────────────────────────────────────────

def _edge_model_authority_src() -> str | None:
    """在同一个 checkout 里找 edge 侧的 model_authority.py。"""
    for parent in Path(__file__).resolve().parents:
        p = (parent / "edge/hermes-plugins/catfish-xcatfish-user"
                     / "model_authority.py")
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def test_edge_and_gateway_agree_on_the_sentinel_literal():
    """edge 侧 `AUTO_SENTINEL` 跟这边的常量必须逐字相同。

    对不上的后果是分岔的: edge 会把 sentinel 当成"员工显式选了模型"
    (model_authority.decide_model 的判据), 从而**挡住 picker** —— 正好是
    P46 存在的理由被反过来了。
    """
    src = _edge_model_authority_src()
    if src is None:
        pytest.skip("同 checkout 下找不到 edge 的 model_authority.py")
    m = re.search(r'^AUTO_SENTINEL\s*=\s*"([^"]+)"', src, re.M)
    assert m, "edge 侧 AUTO_SENTINEL 不见了 —— 上游可能改了机制"
    assert m.group(1) == AUTO_MODEL_SENTINEL, (
        f"edge 是 {m.group(1)!r}, gateway 是 {AUTO_MODEL_SENTINEL!r} —— "
        "两边对不上时 picker 会被 sentinel 挡住 (P46 反转)"
    )
