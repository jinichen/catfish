"""thinking_guard —— 强制 tool_choice 时关掉深度思考 (8/8).

起因: picker 切到 qwen3.8-max 后「早安」页整块打不开 —
    400 The tool_choice parameter does not support being set to required
        or object in thinking mode
而 profile.ts:479 发的正是 tool_choice={type:function,function:submit_profile}。

这里最要紧的是**不要误伤**: 主对话 tool_choice=auto (chat.ts:281), 它没碰到
这条约束, 关了它的思考就是拿模型的推理能力去换一个不相干的修复。
"""

from types import SimpleNamespace

import pytest

from catfish_gateway.thinking_guard import apply, is_forced_tool_choice


def _model(name="m", provider=None, upstream_model="openai/x", api_base=None):
    return SimpleNamespace(
        name=name,
        upstream=SimpleNamespace(
            provider=provider, model=upstream_model, api_base=api_base
        ),
    )


_QWEN = dict(
    provider="dashscope",
    upstream_model="openai/qwen3.8-max",
    api_base="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)
_DS = dict(provider="deepseek", upstream_model="deepseek/deepseek-v4-flash")


# ── tool_choice 形态判定 ───────────────────────────────────


@pytest.mark.parametrize("tc", ["required", "REQUIRED", " required "])
def test_required_是强制(tc):
    assert is_forced_tool_choice(tc) is True


def test_指定函数是强制():
    assert is_forced_tool_choice(
        {"type": "function", "function": {"name": "submit_profile"}}
    ) is True


@pytest.mark.parametrize("tc", ["auto", "none", "AUTO", None, "", {}, [], 0])
def test_auto_none_缺省一律不算强制(tc):
    """主对话走 auto。误判成强制 = 把所有对话的推理都关了。"""
    assert is_forced_tool_choice(tc) is False


# ── 真正的注入 ─────────────────────────────────────────────


def test_qwen_强制tool_choice_关思考():
    p = {"tool_choice": "required"}
    assert apply(p, _model(**_QWEN)) == "enable_thinking=false (dashscope)"
    assert p["enable_thinking"] is False


def test_qwen_指定函数形态也关():
    p = {"tool_choice": {"type": "function", "function": {"name": "submit_profile"}}}
    assert apply(p, _model(**_QWEN))
    assert p["enable_thinking"] is False


def test_qwen_auto_不动():
    """这条是整个模块的底线 —— 主对话必须原样。"""
    p = {"tool_choice": "auto"}
    assert apply(p, _model(**_QWEN)) is None
    assert "enable_thinking" not in p


def test_没有tool_choice字段_不动():
    p = {"messages": []}
    assert apply(p, _model(**_QWEN)) is None
    assert p == {"messages": []}


def test_deepseek_用extra_body():
    p = {"tool_choice": "required"}
    assert apply(p, _model(**_DS)) == "thinking.type=disabled (deepseek)"
    assert p["extra_body"]["thinking"] == {"type": "disabled"}


def test_deepseek_不踩掉extra_body里已有的别的键():
    p = {"tool_choice": "required", "extra_body": {"别的": 1}}
    apply(p, _model(**_DS))
    assert p["extra_body"]["别的"] == 1
    assert p["extra_body"]["thinking"] == {"type": "disabled"}


# ── 不认识的 provider 一律不碰 ─────────────────────────────


@pytest.mark.parametrize(
    "m",
    [
        _model(provider="gemini", upstream_model="gemini/gemini-3.5-flash"),
        _model(provider="internal-llm-qwen-vision", upstream_model="openai/qwen_v3_6",
               api_base="http://10.10.40.102:32730/openapi/x/v1"),
        _model(provider=None, upstream_model="openai/gpt-4o", api_base=None),
    ],
    ids=["gemini", "私有vLLM", "真openai"],
)
def test_不认识的provider不动(m):
    """凭印象编个参数名塞过去, 换来的是一个更难查的 400。宁可不管。"""
    p = {"tool_choice": "required"}
    assert apply(p, m) is None
    assert p == {"tool_choice": "required"}


def test_upstream缺失也不炸():
    p = {"tool_choice": "required"}
    assert apply(p, SimpleNamespace(name="m")) is None


# ── provider 判定的三条依据 ────────────────────────────────


def test_没有provider字段时靠域名认出百炼():
    """老形态 (upstream 里直接写 api_base) 也要认得出 —— dashscope 走 OpenAI
    兼容模式, 前缀是 openai/, 跟真 OpenAI 撞名, 只能看域名。"""
    m = _model(provider=None, upstream_model="openai/qwen3.8-max",
               api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
    p = {"tool_choice": "required"}
    assert apply(p, m)
    assert p["enable_thinking"] is False


def test_靠litellm前缀认出deepseek():
    m = _model(provider=None, upstream_model="deepseek/deepseek-v4-flash")
    p = {"tool_choice": "required"}
    assert apply(p, m)
    assert p["extra_body"]["thinking"] == {"type": "disabled"}


# ── 管理员显式配了就不覆盖 ─────────────────────────────────


def test_管理员显式配了_顶层_不覆盖():
    p = {"tool_choice": "required", "enable_thinking": True}
    assert apply(p, _model(**_QWEN)) is None
    assert p["enable_thinking"] is True, "param_overrides 的语义是 FORCE, 不能被越过"


def test_管理员显式配了_extra_body_不覆盖():
    p = {"tool_choice": "required", "extra_body": {"enable_thinking": True}}
    assert apply(p, _model(**_QWEN)) is None
    assert p["extra_body"]["enable_thinking"] is True


def test_deepseek_已有thinking配置不覆盖():
    """models.yaml 里 deepseek 本来就配了 param_overrides.extra_body.thinking,
    这里必须认出来, 别重复写 (虽然值一样, 但"我以为是我干的"会误导排查)。"""
    p = {"tool_choice": "required",
         "extra_body": {"thinking": {"type": "disabled"}}}
    assert apply(p, _model(**_DS)) is None
