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

别的 provider (gemini / anthropic / 私有 vLLM) **一律不动**。它们要么没这条
约束, 要么参数名不一样 —— 凭印象编一个参数名塞过去, 换来的是一个更难查的
400。宁可不管。

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


def _provider_of(model: Any) -> str:
    """判这个模型属于哪家。返 "dashscope" / "deepseek" / ""(不认识)。

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


def apply(params: dict[str, Any], model: Any) -> str | None:
    """就地处理 params。返回做了什么的描述 (给日志), 没动返 None。

    调用点在 _build_litellm_params 末尾 —— 必须在 param_overrides 之后,
    这样才判得出管理员有没有显式配过。
    """
    if not is_forced_tool_choice(params.get("tool_choice")):
        return None

    provider = _provider_of(model)
    if not provider:
        return None

    if _already_configured(params, provider):
        logger.warning(
            "%s 这次请求强制了 tool_choice, 但 param_overrides 里已显式设了思考开关 —— "
            "尊重管理员配置不覆盖。若那个值是「开」, 上游大概率返 400 "
            "(thinking 与强制 tool_choice 互斥)。",
            getattr(model, "name", "?"),
        )
        return None

    if provider == "dashscope":
        # 顶层传 —— 8/8 打网关实测生效 (见模块头)。
        params["enable_thinking"] = False
        return "enable_thinking=false (dashscope)"

    # deepseek: 放 extra_body, 跟 models.yaml 里既有的 param_overrides 同款写法
    eb = params.get("extra_body")
    if not isinstance(eb, dict):
        eb = {}
    eb["thinking"] = {"type": "disabled"}
    params["extra_body"] = eb
    return "thinking.type=disabled (deepseek)"
