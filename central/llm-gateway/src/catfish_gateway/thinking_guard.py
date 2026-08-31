"""强制 tool_choice 与"深度思考"互斥时, 自动关掉思考 (8/8).

## 现象

picker 切到 qwen3.8-max 之后, Companion 的「早安」页整块打不开:

    advisor 综合判断暂不可用 (LLM 返结构非预期)

网关日志:

    catfish_source=companion-profile  tools=['submit_profile']
    400 invalid_parameter_error:
      The tool_choice parameter does not support being set to required
      or object in thinking mode

## 这不是新问题, 是同一条约束的第二次

**6/30 鸿波已经为 DeepSeek 踩过一遍**, 结论写在 models.yaml 的 deepseek 块里:

    1. POST /v1 + thinking 不带 (默认开) + tool_choice={type:function,...}
       → 报错 "Thinking mode does not support this tool_choice"
    2. POST /v1 + thinking={type:disabled} + 同款 tool_choice
       → 完整工作

当时的修法是给 deepseek 配 `param_overrides.extra_body.thinking.type=disabled`
—— **按模型一刀切关掉思考**。Qwen 是同一条约束, 但没人给它配, 于是换模型的
那一刻整条路径就断了。

## 为什么不照抄"按模型关掉"

profile.ts:715 写着 `model: 必传, 跟员工 chat model 同款
(5/17 BL-INTERNAL-MODEL-FOLLOW-USER)` —— profile / advisor / wikiLinkSuggest
这几条**故意**跟着 picker 走。所以:

  · 建一个"不思考"的孪生模型再让它们指过去 —— 客户端根本不看 role, 没用
  · 给 qwen-flash 整个关掉思考 —— 主对话的推理能力一起没了, 而主对话的
    tool_choice 是 auto (chat.ts:281), 本来完全不受这条约束影响

约束的真实形状是「**thinking ⊥ 强制 tool_choice**」, 不是「这个模型不能思考」。
按后者去配, 就是拿一个模型级的钝刀去砍一个请求级的问题 —— 代价落在
完全无辜的主对话上。

所以这里按**请求**判: 只有这一次请求真的强制了 tool_choice, 才关这一次的思考。

## 只处理验证过的两家

  dashscope   enable_thinking=false
              8/8 实测 (打自己的网关, 同一道 "1+1=?"):
                默认      reasoning 83 字, completion 31 tok, 2.118s
                关掉后    reasoning 无,   completion  7 tok, 0.701s
              顶层传就生效 —— litellm 的 openai handler 会带过去。
              没用 extra_body 是因为**那条路径没验过**, 而这条验过了。

  deepseek    thinking={"type": "disabled"}, 放 extra_body
              6/30 鸿波 curl 实测 (见 models.yaml)。

  qwen 自建   chat_template_kwargs={"enable_thinking": false}, 放 extra_body
              8/10 鸿波打内网端点实测, 三种写法同一道 "1+1=?" max_tokens=100:
                enable_thinking=false (顶层)      content ''        ← 无效
                chat_template_kwargs.enable_thinking  content '1 + 1 = 2'  ← 就它
                thinking.type=disabled            content ''        ← 那是 deepseek 的
              这是 Qwen3 在 vLLM / SGLang 上的官方开关, 跟百炼那套**不一样** ——
              同样叫 qwen, 托管方式不同参数就不同, 所以要跟 dashscope 分开认。

别的 provider (gemini / anthropic / 其它自建) **一律不动**。它们要么没这条
约束, 要么参数名不一样 —— 凭印象编一个参数名塞过去, 换来的是一个更难查的
400。宁可不管。

## 三家关的理由不同, 但判据是同一条

  dashscope / deepseek   不关直接 400 (thinking ⊥ 强制 tool_choice)
  qwen 自建              不会 400, 但结构化输出不需要思考, 关了明显快

判据都是「**这次请求强制了 tool_choice**」= 我要一个结构化结果。

## ⚠ 8/10 走过一次弯路, 别再走

当天下午一度把判据放宽成「**只要是内部调用 (有 X-Catfish-Source) 就关**」,
想顺手治好 advisor 慢 (41 秒 / 偶发 60 秒超时) 的问题。**当晚被现场推翻**:

    关之前 (思考开)  finish_reason=stop       ← 会收尾, 给出 final content
    关之后 (思考关)  finish_reason=tool_calls ← 10 轮全是, total 涨到 38
                                                还在调工具, 永远不收尾

advisor Call 1 是 hermes agent loop (tool_choice **故意是 auto**, 见
briefing_advisor.ts:1518)。**这个模型关掉思考之后不会自己收尾** —— 拿不到
final content, JSON 解析必然失败, 早安页反而更坏。

同一批日志里 profile (tools_count=1, 强制 tool_choice) 关掉后 743 token
正常返回。所以分界线不是「内不内部」, 是 **single-shot 还是 agent loop**,
而强制 tool_choice 正好就是 single-shot 的标志。

当时支持放宽的理由是"agent loop 有多轮纠错机会, 单轮少想一点损失有限" ——
**纯推理, 没有数据**, 而数据正好相反。

教训: 改一个会影响**模型行为**的开关, 要拿 finish_reason 分布这种硬指标验,
不能拿"应该没事"验。内部调用慢是另一个问题, 归 timeout 管 (已调 180 秒)。

## 管理员显式配了就不覆盖

`param_overrides` 的文档写着 "FORCE on every request"。管理员显式写了思考
开关就是他的意思, 这里不越过他 —— 但会 WARNING 一声说这次请求大概率 400,
因为静默失败正是这一整天在反复出现的那类问题。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("catfish.gateway.thinking_guard")


def is_forced_tool_choice(tool_choice: Any) -> bool:
    """这次请求是不是"必须调工具"。

    OpenAI 协议里 tool_choice 有四种取值:

        "none"      不许调        ← 不受约束
        "auto"      自己决定      ← 不受约束 (主对话走这条, chat.ts:281)
        "required"  必须调某个    ← 受约束
        {"type": "function", "function": {"name": ...}}  必须调指定的  ← 受约束

    后两种才跟 thinking 互斥。前两种绝不能误伤 —— 那会把主对话的推理关掉,
    而它根本没碰到这条约束。
    """
    if isinstance(tool_choice, str):
        return tool_choice.strip().lower() == "required"
    # 对象形态: 指定具体函数 (profile.ts:479 用的就是这种)
    return isinstance(tool_choice, dict) and bool(tool_choice)


#: 哪几家我们**验证过**关思考的写法。见模块头的实测记录。
_KNOWN_DISABLE_FORMS = ("dashscope", "deepseek", "qwen_selfhost")

#: 强制 tool_choice 时该关思考的 provider。理由**两家不同**, 但结论一样:
#:
#:   dashscope / deepseek  不关直接 400 (thinking ⊥ 强制 tool_choice, 8/8 实测)
#:   qwen 自建             不会 400, 但结构化输出不需要思考, 关了明显快
#:                         (8/10: profile 关掉后 743 token 正常返回)
_DISABLE_ON_FORCED_TOOL_CHOICE = ("dashscope", "deepseek", "qwen_selfhost")



def _provider_of(model: Any) -> str:
    """判这个模型属于哪家。返 "dashscope" / "deepseek" / "qwen_selfhost" / ""(不认识)。

    三个依据, 从可靠到兜底:
      1. upstream.provider —— 8/1 供应商拆分引入的正式字段, merge_provider
         合并时**保留**不删 (config_providers.py:89), 所以运行时拿得到
      2. upstream.model 的 litellm 前缀 —— deepseek/ 是明确的
      3. api_base 的域名 —— dashscope 走 OpenAI 兼容模式, 前缀是 `openai/`,
         跟真 OpenAI 撞名, 只能靠域名区分

    认不出来就返空, 上层什么都不做。
    """
    up = getattr(model, "upstream", None)
    if up is None:
        return ""

    pid = (getattr(up, "provider", None) or "").strip().lower()
    if "dashscope" in pid:
        return "dashscope"
    if "deepseek" in pid:
        return "deepseek"

    um = (getattr(up, "model", "") or "").lower()
    if um.startswith("deepseek/"):
        return "deepseek"

    base = (getattr(up, "api_base", None) or "").lower()
    # 百炼的两种域名都见过: dashscope.aliyuncs.com / <实例>.maas.aliyuncs.com
    if "aliyuncs.com" in base:
        return "dashscope"
    if "api.deepseek.com" in base:
        return "deepseek"

    # 自建 Qwen (vLLM / SGLang)。**必须放在 aliyuncs 判断之后** —— 百炼上的
    # qwen 也叫 qwen, 但那边认 enable_thinking 顶层, 这边认 chat_template_kwargs,
    # 顺序反了就会给内网发百炼的参数 (8/10 实测那条是无效的, 静默不生效)。
    #
    # 判据是"名字里有 qwen 且不在百炼域名下"。这是从内网那一个端点
    # (openai/qwen_v3_6_35b_a3b @ 10.10.40.102) 推广出来的 —— 推广的依据是
    # chat_template_kwargs.enable_thinking 是 Qwen3 在 vLLM/SGLang 上的官方开关,
    # 自建 qwen 走的基本都是这两个引擎。真遇到第三种托管方式而它不认这个参数,
    # 表现是**静默不生效** (多花点 token), 不是 400 —— 代价可控才敢推广。
    if "qwen" in um:
        return "qwen_selfhost"
    return ""


def _already_configured(params: dict[str, Any], provider: str) -> bool:
    """管理员是不是已经在 param_overrides 里显式设过思考开关。"""
    if provider == "dashscope":
        if "enable_thinking" in params:
            return True
        eb = params.get("extra_body")
        return isinstance(eb, dict) and "enable_thinking" in eb
    if provider == "deepseek":
        if "thinking" in params:
            return True
        eb = params.get("extra_body")
        return isinstance(eb, dict) and "thinking" in eb
    return False


def _put_extra_body(params: dict[str, Any], key: str, value: Any) -> None:
    """往 extra_body 里塞一项, 保留already有的其它项。"""
    eb = params.get("extra_body")
    if not isinstance(eb, dict):
        eb = {}
    eb[key] = value
    params["extra_body"] = eb


def disable_thinking(params: dict[str, Any], model: Any) -> str | None:
    """就地给 params 加上"关掉深度思考"的参数。返描述 (给日志), 不认识返 None。

    **无条件关**, 不看 tool_choice —— 供需要结构化结果但不需要推理过程的
    单次内部调用复用。

    认不出 provider 就什么都不做 —— 编一个参数名塞过去只会换来更难查的 400。
    """
    provider = _provider_of(model)
    if provider not in _KNOWN_DISABLE_FORMS:
        return None
    if _already_configured(params, provider):
        return None

    if provider == "dashscope":
        params["enable_thinking"] = False
        return "enable_thinking=false (dashscope)"
    if provider == "deepseek":
        _put_extra_body(params, "thinking", {"type": "disabled"})
        return "thinking.type=disabled (deepseek)"
    # qwen_selfhost —— 8/10 实测唯一有效的写法, 见模块头
    _put_extra_body(params, "chat_template_kwargs", {"enable_thinking": False})
    return "chat_template_kwargs.enable_thinking=false (qwen 自建)"



def apply(params: dict[str, Any], model: Any) -> str | None:
    """就地处理 params。返回做了什么的描述 (给日志), 没动返 None。

    调用点在 _build_litellm_params 末尾 —— 必须在 param_overrides 之后,
    这样才判得出管理员有没有显式配过。

    判据只有一条: **这次请求强制了 tool_choice**, 也就是"我要一个结构化结果"。
    那种场景思考没有价值 —— 要么上游直接 400 (dashscope/deepseek), 要么白白
    慢一截 (qwen 自建)。

    ── ⚠ 8/10 走过一次弯路, 写下来免得再犯 ─────────────────────────────
    当天下午一度把判据放宽成"**只要是内部调用 (有 X-Catfish-Source) 就关**",
    想顺手治好 advisor 慢的问题。**当晚就被现场推翻**:

        关之前 (思考开)  finish_reason=stop      ← 会收尾, 给出 final content
        关之后 (思考关)  finish_reason=tool_calls ← 10 轮全是, total 涨到 38
                                                    还在调工具, 永远不收尾

    advisor Call 1 是 hermes agent loop (tool_choice **故意是 auto**, 见
    briefing_advisor.ts:1518)。**这个模型关掉思考之后不会自己收尾** —— 一直
    调工具, 拿不到 final content, JSON 解析必然失败, 早安页反而更坏。

    而同一批日志里, profile (tools_count=1, 强制 tool_choice) 关掉思考后
    743 token 正常返回。所以分界线不是"内不内部", 是 **single-shot 还是
    agent loop** —— 而强制 tool_choice 正好就是 single-shot 的标志。

    当时我的理由是"agent loop 有多轮纠错机会, 单轮少想一点损失有限"。
    **这是纯推理, 没有数据**, 而数据正好相反。教训: 改一个会影响模型行为的
    开关, 拿 finish_reason 分布这种硬指标验, 别拿"应该没事"验。

    (内部调用慢的问题另想办法 —— 提高 timeout 已经做了 180 秒, 那条路是对的。)
    """
    if not is_forced_tool_choice(params.get("tool_choice")):
        return None

    provider = _provider_of(model)
    if provider not in _DISABLE_ON_FORCED_TOOL_CHOICE:
        return None

    if _already_configured(params, provider):
        logger.warning(
            "%s 这次请求强制了 tool_choice, 但 param_overrides 里已显式设了思考开关 —— "
            "尊重管理员配置不覆盖。若那个值是「开」, 上游大概率返 400 "
            "(thinking 与强制 tool_choice 互斥)。",
            getattr(model, "name", "?"),
        )
        return None

    # 走跟 disable_thinking 同一套 provider 语法, 免得两处各写一份再各改一半
    return disable_thinking(params, model)
