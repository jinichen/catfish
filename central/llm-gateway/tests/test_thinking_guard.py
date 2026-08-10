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


# ─────────────────────────────────────────────────────────────────────
# 8/10: qwen 自建 (内网 Qwen3-VL) —— 压缩静默失败那次的根因
#
# 现场: 压缩器用员工同款模型发 max_tokens=800 的摘要请求, 800 全被 reasoning
# 吃光, content 空 → 压缩静默失败 → 449 条原样发给 128K 模型 → 400。
#
# 参数是打内网端点实测出来的 (同一道 "1+1=?", max_tokens=100):
#     enable_thinking=false (顶层)          content ''          ← 无效
#     chat_template_kwargs.enable_thinking  content '1 + 1 = 2' ← 就它
#     thinking.type=disabled                content ''          ← 那是 deepseek 的
# ─────────────────────────────────────────────────────────────────────

from catfish_gateway.thinking_guard import disable_thinking  # noqa: E402

# 内网这个**没有 provider 字段** (models.yaml 里就没配), 只能靠 upstream_model
# 里的 "qwen" + api_base 不是 aliyuncs 来认 —— 正是真实形态。
_QWEN_SELFHOST = dict(
    upstream_model="openai/qwen_v3_6_35b_a3b",
    api_base="http://10.10.40.102:32730/openapi/x/v1/",
)

# 百炼但**不带 provider 字段**的形态 —— 靠域名认。跟自建的区分全靠判定顺序:
# aliyuncs 要先判, 否则名字里的 qwen 会先命中自建分支。
_QWEN_DASH_NO_PROVIDER = dict(
    upstream_model="openai/qwen3.8-max",
    api_base="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)


def test_qwen自建用_chat_template_kwargs():
    p = {}
    assert disable_thinking(p, _model(**_QWEN_SELFHOST))
    assert p["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # 另外两种写法实测无效, 出现即是错
    assert "enable_thinking" not in p, "顶层 enable_thinking 是百炼的, 内网无效"
    assert "thinking" not in p.get("extra_body", {}), "thinking.type 是 deepseek 的"


def test_百炼qwen和自建qwen不能混():
    """两边都叫 qwen, 参数**不一样** —— 判定顺序错了就静默不生效。"""
    dash = {}
    disable_thinking(dash, _model(**_QWEN_DASH_NO_PROVIDER))
    selfhost = {}
    disable_thinking(selfhost, _model(**_QWEN_SELFHOST))
    assert dash.get("enable_thinking") is False
    assert "chat_template_kwargs" not in dash.get("extra_body", {})
    assert selfhost["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "enable_thinking" not in selfhost


def test_qwen自建不进_tool_choice_自动关闭名单():
    """「知道怎么关」推不出「应该关」。

    _KNOWN_DISABLE_FORMS 有它 (上面那条测过), 但 thinking ⊥ 强制 tool_choice
    这条约束在自建 qwen 上**没验过**。把两者混成一个判断, 就会因为"知道怎么关"
    顺手把 advisor / profile 那几条路径的思考也关掉 —— 那是没根据的改行为。
    """
    assert apply({"tool_choice": "required"}, _model(**_QWEN_SELFHOST)) is None
    assert apply({"tool_choice": {"type": "function", "function": {"name": "f"}}},
                 _model(**_QWEN_SELFHOST)) is None


def test_不认识的provider一律不动():
    """编一个参数名塞过去 = 更难查的 400。宁可不管。"""
    for m in ({"upstream_model": "gemini/gemini-2.5-pro"},
              {"upstream_model": "anthropic/claude"}, {}):
        p = {}
        assert disable_thinking(p, _model(**m)) is None
        assert p == {}, "认不出还动了 params"
