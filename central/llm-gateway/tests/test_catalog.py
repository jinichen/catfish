"""catalog.build_catalog 单测 —— 重点是三态状态字段 (api_key_configured /
is_reachable / status_reason) 在不同 env / upstream_status 组合下的输出。

历史踩坑:
    Companion 仪表盘曾出现"Qwen 是绿,Gemini 是不可达"的反向显示, 根因是
    is_reachable=None 时前端把它当 false 渲染。现在的契约: None != False, 前端
    对 None 应当渲染成"未知/还没探"而不是"不可达"。

测试矩阵:
    1. 配 key + reachable=True   → 三态全到位 ✓
    2. 配 key + reachable=False  → api_key_configured=True, is_reachable=False
    3. 配 key + 没 cache         → is_reachable=None (gateway 启动后 race)
    4. 没配 key                  → api_key_configured=False, status_reason 是统一 "API key 未配"
    5. 私有模型 + 公共模型混合  → default 选有 key 的
    6. embedding 模式不出现在 catalog (那是管道类)
    7. 匿名 user (None) 也能拿到列表, authenticated=False
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from catfish_gateway.catalog import build_catalog


# ---------- helpers ----------

def _model(
    name: str,
    *,
    tier: str = "private",
    mode: str = "chat",
    default: bool = False,
    api_key_configured: bool = True,
    display_name: str | None = None,
    recommended_for: list[str] | None = None,
    context_window: int | None = 128_000,
    cost_tier: str = "free",
    supports_tool_use: bool = True,
    supports_vision: bool = False,
):
    """构造一个 ModelConfig duck-type, build_catalog 只读这些字段。"""
    return SimpleNamespace(
        name=name,
        tier=tier,
        mode=mode,
        default=default,
        display_name=display_name or name,
        recommended_for=recommended_for or ["general"],
        context_window=context_window,
        cost_tier=cost_tier,
        supports_tool_use=supports_tool_use,
        supports_vision=supports_vision,
        upstream=SimpleNamespace(is_available=api_key_configured),
    )


def _config(*models) -> Any:
    return SimpleNamespace(models=list(models))


def _user(can_access_all: bool = True):
    """模拟 User —— 只用 can_access 一个方法"""
    return SimpleNamespace(can_access=lambda m: can_access_all)


# ---------- 三态: 配 key + reachable=True ----------

def test_configured_and_reachable_is_green() -> None:
    cfg = _config(_model("m1", default=True))
    status = {"m1": {"reachable": True, "reason": "TCP 通"}}
    out = build_catalog(cfg, _user(), status)

    assert len(out["models"]) == 1
    m = out["models"][0]
    assert m["api_key_configured"] is True
    assert m["is_reachable"] is True
    assert m["status_reason"] == "TCP 通"
    assert out["default"] == "m1"


# ---------- 三态: 配 key + reachable=False ----------

def test_configured_but_unreachable_is_yellow() -> None:
    cfg = _config(_model("m1"))
    status = {"m1": {"reachable": False, "reason": "代理不通"}}
    out = build_catalog(cfg, _user(), status)
    m = out["models"][0]
    assert m["api_key_configured"] is True
    assert m["is_reachable"] is False
    assert m["status_reason"] == "代理不通"


# ---------- 三态: 配 key + 没 cache ----------

def test_configured_but_no_cache_yields_none_reachable() -> None:
    """gateway 启动初期, upstream_status 还没探完 → is_reachable=None
    前端必须按 None 渲染'未知', 不能渲染成红色"""
    cfg = _config(_model("m1"))
    out = build_catalog(cfg, _user(), upstream_status=None)
    m = out["models"][0]
    assert m["is_reachable"] is None
    assert m["api_key_configured"] is True


def test_configured_but_empty_cache_yields_none_reachable() -> None:
    """upstream_status 是 {} 同上"""
    cfg = _config(_model("m1"))
    out = build_catalog(cfg, _user(), upstream_status={})
    m = out["models"][0]
    assert m["is_reachable"] is None


# ---------- 三态: 没配 key ----------

def test_no_api_key_uses_unified_reason() -> None:
    cfg = _config(_model("m1", api_key_configured=False))
    # 即使 cache 里有 reason, 也该被覆盖为统一的 "API key 未配"
    status = {"m1": {"reachable": True, "reason": "这条不应该被用"}}
    out = build_catalog(cfg, _user(), status)
    m = out["models"][0]
    assert m["api_key_configured"] is False
    assert "API key 未配" in m["status_reason"]


# ---------- default 选取 ----------

def test_default_picks_marked_default_with_key() -> None:
    cfg = _config(
        _model("a", api_key_configured=True),
        _model("b", default=True, api_key_configured=True),
        _model("c"),
    )
    out = build_catalog(cfg, _user())
    assert out["default"] == "b"


def test_default_skips_marked_default_when_no_key() -> None:
    """default=True 但没 key, 应该退回到第一个有 key 的"""
    cfg = _config(
        _model("nokey", default=True, api_key_configured=False),
        _model("hask", api_key_configured=True),
    )
    out = build_catalog(cfg, _user())
    assert out["default"] == "hask"


def test_default_falls_back_to_first_when_all_no_key() -> None:
    cfg = _config(
        _model("a", api_key_configured=False),
        _model("b", api_key_configured=False),
    )
    out = build_catalog(cfg, _user())
    assert out["default"] == "a"


# ---------- embedding 不出现在 catalog ----------

def test_embedding_mode_excluded() -> None:
    cfg = _config(
        _model("chat-1", mode="chat"),
        _model("embed-1", mode="embedding"),
        _model("chat-2", mode="chat"),
    )
    out = build_catalog(cfg, _user())
    ids = [m["id"] for m in out["models"]]
    assert "chat-1" in ids
    assert "chat-2" in ids
    assert "embed-1" not in ids


# ---------- 匿名 user ----------

def test_anonymous_user_returns_authenticated_false() -> None:
    cfg = _config(_model("m1", default=True))
    out = build_catalog(cfg, user=None)
    assert out["authenticated"] is False
    assert len(out["models"]) == 1


def test_authenticated_user_returns_authenticated_true() -> None:
    cfg = _config(_model("m1"))
    out = build_catalog(cfg, _user())
    assert out["authenticated"] is True


# ---------- can_access 过滤 ----------

def test_user_cannot_access_filters_out() -> None:
    cfg = _config(
        _model("public-1", tier="public"),
        _model("private-1", tier="private"),
    )
    # user 只能访问 private
    user = SimpleNamespace(
        can_access=lambda m: m.tier == "private",
    )
    out = build_catalog(cfg, user)
    ids = [m["id"] for m in out["models"]]
    assert ids == ["private-1"]


# ---------- 字段完整性 ----------

def test_model_entry_has_all_required_fields() -> None:
    cfg = _config(
        _model(
            "full",
            display_name="Full Model · 测试",
            tier="public",
            recommended_for=["fast", "cheap"],
            context_window=2_000_000,
            cost_tier="paid",
            supports_tool_use=True,
            supports_vision=True,
        ),
    )
    out = build_catalog(cfg, _user())
    m = out["models"][0]
    expected_keys = {
        "id",
        "display_name",
        "tier",
        "recommended_for",
        "context_window",
        "cost_tier",
        "supports_tool_use",
        "supports_vision",
        "api_key_configured",
        "is_reachable",
        "status_reason",
    }
    assert expected_keys.issubset(m.keys()), (
        f"missing fields: {expected_keys - m.keys()}"
    )


# ---------- mixed real-world: catfish-gateway 实际 5 模型场景 ----------

def test_real_world_5_models_mixed_state() -> None:
    """模拟 .env 配了 INTERNAL_LLM_KEY 没配 GEMINI_API_KEY 的常见场景"""
    cfg = _config(
        _model("catfish-private-main", default=True, tier="private",
               api_key_configured=True),
        _model("catfish-private-vision", tier="private",
               api_key_configured=True),
        _model("catfish-private-embed", tier="private", mode="embedding",
               api_key_configured=True),
        _model("catfish-public-gemini-pro", tier="public",
               api_key_configured=False),
        _model("catfish-public-gemini-flash", tier="public",
               api_key_configured=False),
    )
    status = {
        "catfish-private-main": {"reachable": True, "reason": "TCP 通"},
        "catfish-private-vision": {"reachable": False, "reason": "TimeoutError"},
        # gemini 没配 key 时, gateway 不会探, cache 里没条目
    }
    out = build_catalog(cfg, _user(), status)

    # embedding 隐藏, 其它 4 个出现
    ids = [m["id"] for m in out["models"]]
    assert "catfish-private-embed" not in ids
    assert len(out["models"]) == 4

    main = next(m for m in out["models"] if m["id"] == "catfish-private-main")
    assert main["api_key_configured"] is True
    assert main["is_reachable"] is True

    vision = next(
        m for m in out["models"] if m["id"] == "catfish-private-vision"
    )
    assert vision["api_key_configured"] is True
    assert vision["is_reachable"] is False

    gemini_pro = next(
        m for m in out["models"] if m["id"] == "catfish-public-gemini-pro"
    )
    assert gemini_pro["api_key_configured"] is False
    assert "API key 未配" in gemini_pro["status_reason"]

    # default 选 main (有 key + 标了 default)
    assert out["default"] == "catfish-private-main"
