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
        _model(provider=None, upstream_model="openai/gpt-4o", api_base=None),
    ],
    # 8/10: "私有vLLM" 从这里移走了 —— 名字带 qwen 的自建端点现在**认得出**
    # (_provider_of → qwen_selfhost), 强制 tool_choice 时该关。见文件末尾
    # test_qwen自建_强制tool_choice_要关。这里只留真正认不出的。
    ids=["gemini", "真openai"],
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


def test_qwen自建_强制tool_choice_也关_八月十日翻案():
    """⚠ 这条测试**翻过案**, 两次都有据, 记下来免得来回改。

    8/10 上午写的版本断言"qwen 自建**不进** apply 名单", 理由是"只验了怎么关,
    没验不关会不会 400 —— 知道怎么关推不出应该关"。那个理由在当时是对的:
    没有数据支持关它。

    8/10 晚有数据了。现场同一批日志:
        profile (tools_count=1, 强制 tool_choice) 关掉思考 → 743 token 正常返回
        advisor Call 1 (tool_choice=auto, agent loop) 关掉思考 → 永不收尾 ✗

    所以判据不是"哪家 provider", 是"**这次请求要不要结构化结果**"。
    强制 tool_choice = 要结构化 = 思考没价值, 三家都一样。
    qwen 自建那条的理由从"不关会 400"换成"关了明显快", 结论相同。
    """
    for tc in ("required", {"type": "function", "function": {"name": "f"}}):
        p = {"tool_choice": tc}
        assert apply(p, _model(**_QWEN_SELFHOST)), f"{tc!r} 该关没关"
        assert p["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_不认识的provider一律不动():
    """编一个参数名塞过去 = 更难查的 400。宁可不管。"""
    for m in ({"upstream_model": "gemini/gemini-2.5-pro"},
              {"upstream_model": "anthropic/claude"}, {}):
        p = {}
        assert disable_thinking(p, _model(**m)) is None
        assert p == {}, "认不出还动了 params"


# ─────────────────────────────────────────────────────────────────────
# 8/10: qwen 自建进「强制 tool_choice 就关」名单 + 那次被推翻的弯路
#
# 白天的现场: advisor 慢 (41 秒) 偶发 60 秒超时, 早安页打不开。
# 一度把判据放宽成"只要是内部调用就关思考", **当晚被推翻**:
#
#     关之前 (思考开)  finish_reason=stop       ← 会收尾
#     关之后 (思考关)  finish_reason=tool_calls ← 10 轮全是, 永远不收尾
#
# advisor Call 1 是 agent loop (tool_choice 故意 auto)。这个模型关掉思考
# 之后不会自己收尾 —— 拿不到 final content, JSON 解析必然失败。
# 而 profile (强制 tool_choice, single-shot) 关掉后正常返回。
#
# 所以分界线是 **single-shot vs agent loop**, 判据就是强制 tool_choice。
# ─────────────────────────────────────────────────────────────────────

_QW_SELF = dict(
    upstream_model="openai/qwen_v3_6_35b_a3b",
    api_base="http://10.10.40.102:32730/x/v1",
)


def test_qwen自建_强制tool_choice_要关():
    """profile / wiki-suggest / advisor Call 2 这类 single-shot 结构化输出。"""
    for tc in ("required", {"type": "function", "function": {"name": "submit_profile"}}):
        p = {"tool_choice": tc}
        assert apply(p, _model(**_QW_SELF)), f"tool_choice={tc!r} 没关"
        assert p["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_agent_loop_绝不能关_哪怕是内部调用():
    """这条钉的是 8/10 那次事故。

    advisor Call 1 是内部调用 (有 X-Catfish-Source), 但 tool_choice=auto ——
    **关掉思考它就不收尾了**, finish_reason 永远是 tool_calls, total 涨到 38
    还在调工具, JSON 拿不到, 早安页比不改之前更坏。

    "是内部调用" 推不出 "该关思考"。能推出的只有 "要结构化结果 (强制
    tool_choice)" → "思考没用"。
    """
    for tc in ("auto", None, "none"):
        p = {"tool_choice": tc}
        assert apply(p, _model(**_QW_SELF)) is None, (
            f"tool_choice={tc!r} 被关了 —— agent loop 会停不下来"
        )
        assert p == {"tool_choice": tc}, "params 被动了"


def test_主对话不受影响():
    """员工聊天 tool_choice=auto, 推理能力必须留着。
    误关没有任何报错, 员工只会觉得"小鲶变笨了"。"""
    for cfg in (_QW_SELF, _QWEN, _DS):
        assert apply({"tool_choice": "auto"}, _model(**cfg)) is None


def test_不认识的provider一律不动_强制也不动():
    for m in ({"upstream_model": "gemini/gemini-2.5-pro"}, {"upstream_model": "x/y"}):
        p = {"tool_choice": "required"}
        assert apply(p, _model(**m)) is None
        assert p == {"tool_choice": "required"}, "认不出还动了 params"


def test_八八原始场景没回归():
    """dashscope / deepseek 强制 tool_choice → 仍要关 (不关直接 400)。"""
    for cfg in (_QWEN, _DS):
        assert apply({"tool_choice": "required"}, _model(**cfg))
