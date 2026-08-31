"""发给上游前的 messages 规范化 (8/8).

## 为什么要有这一层

8/8 早上把 chat_default 从 deepseek 切到 dashscope 的 qwen-flash (deepseek
余额不足), 每一次对话立刻 400:

    {"error":{"code":"invalid_parameter_error",
              "message":"Empty tool_calls is not supported in message."}}

**不是额度问题, 也不是 token plan 没申请下来** —— 鉴权不通会是 401/403,
可达性自检那一行也是 HTTP 200。是请求体里有 assistant message 带着
`"tool_calls": []`。

同一份 messages, deepseek 收了三个月没吭声。这类"某个字段给了但是空的"
在 OpenAI 兼容协议里各家宽严不一:

    deepseek / OpenAI  空数组当没给, 忽略
    dashscope          校验 tool_calls 非空, 空的直接 400

所以它不是"换到百炼才有的 bug", 而是**一直都在, 换了家严格的才露出来**。
BL-COMPRESS-BOUNDARY (5/15 撞 Qwen 122B 400) 是同一类事: 上游对 message
形状的校验比我们以为的严。

## 为什么放在 _build_litellm_params 里

那是三条上游调用路径 (流式 / 非流式 / 内部自调) 唯一的收口点。放在入口
(BL-FIX2 pre-unwrap 那儿) 不够 —— 压缩、model handoff、lean inject 都在
之后动 messages, 任何一步都可能再造出空字段。规范化要贴着出口做。

## 只做"删掉本来就没有意义的字段", 不做语义改写

空的 tool_calls 表达的就是"这条没有工具调用", 删掉它跟不给它完全等价。
这一层不碰非空的 tool_calls, 不补 tool_call_id, 不动配对关系 —— 工具调用
配对由上游会话层维护。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: 这几个字段"给了但是空的"等价于没给, 而部分上游 (dashscope) 会因此 400。
#: function_call 是 OpenAI 老版写法, 一并处理 —— 同样是空了就没意义。
_EMPTY_OK_TO_DROP = ("tool_calls", "function_call")


def drop_empty_tool_calls(messages: Any) -> tuple[Any, int]:
    """删掉 message 里"存在但为空"的 tool_calls / function_call。

    返 (新 messages, 改动条数)。没有要改的就**原样返回同一个对象** ——
    每次请求都深拷一遍 400 条 messages 不值当。

    只在真要改的那条上做浅拷贝: params 里的 messages 可能跟调用方 (以及
    fallback 重试时的下一轮) 共享, 原地改会写穿。
    """
    if not isinstance(messages, list):
        return messages, 0

    hits = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, dict)
        # `in` 而不是 get(): 要的正是"键在、值是空的"这种情况。
        # 值非空 (真有工具调用) 不动; 键不在 更不用动。
        and any(k in m and not m[k] for k in _EMPTY_OK_TO_DROP)
    ]
    if not hits:
        return messages, 0

    out = list(messages)
    for i in hits:
        m = dict(out[i])
        for k in _EMPTY_OK_TO_DROP:
            if k in m and not m[k]:
                del m[k]
        # assistant 删完之后可能既没 content 也没 tool_calls —— 那是另一种
        # "空消息", Qwen 系一样嫌。给空串, 跟 _strip_orphan_tool_boundary
        # 里的处理保持一致。
        if m.get("role") == "assistant" and m.get("content") is None:
            m["content"] = ""
        out[i] = m
    return out, len(hits)


def collapse_extra_system(messages: Any) -> tuple[Any, int, int]:
    """保证整个 messages 里**只有一条 system**。返 (新列表, 合并数, 降级数)。

    ── 为什么 (8/10 实测) ─────────────────────────────────────────────
    内网 Qwen (10.10.40.102 那个 Go 网关) 只认一条 system, 多了就返

        error: code = 400 reason =  message =  metadata = map[] cause = <nil>

    reason / message 全空, 什么都不告诉你。同一天为这个 400 做了两轮八个探针
    都没复现, 最后靠 request_shape_dump 抓到真身才定位。打端点逐字复现过:

        [system, user]                  ✅
        [system, user, system, user]    ❌ 逐字复现线上那个 400
        [system, user,  user , user]    ✅   ← 只差中间那条的 role
        [system, system, user]          ❌   ← 连开头连着两条也不行

    所以约束是「**全局只能一条 system**」, 不是「system 必须在开头」。

    ── 两条规则, 各自保语义 ───────────────────────────────────────────
      开头连续的 system  → 合并成一条 (identity_inject / model_handoff 都是
                          insert(0), 合并后位置和语义都不变)
      后面出现的 system  → **降级成 user** (中间系统片段的位置有时序意义,
                          合并到开头会让它跑到所描述的内容前面去, 所以只能
                          原地降级)

    ── 为什么不按 provider 分 ─────────────────────────────────────────
    "只有一条 system" 对任何 OpenAI 兼容上游都是**合法**的, 一刀切不会给
    deepseek / dashscope 引入新问题, 只是做了点它们不需要的规范化。
    反过来按 provider 分, 就得维护一张"谁认几条 system"的表 —— 而这张表
    只有踩了才知道, 正是今天耗掉一上午的那种东西。
    """
    if not isinstance(messages, list) or not messages:
        return messages, 0, 0

    def _is_sys(m: Any) -> bool:
        return isinstance(m, dict) and m.get("role") == "system"

    if sum(1 for m in messages if _is_sys(m)) <= 1:
        return messages, 0, 0

    # 开头连续段
    head_n = 0
    while head_n < len(messages) and _is_sys(messages[head_n]):
        head_n += 1

    out: list[Any] = []
    merged = 0
    if head_n > 0:
        parts = [str(m.get("content") or "") for m in messages[:head_n]]
        first = dict(messages[0])
        first["content"] = "\n\n".join(p for p in parts if p)
        out.append(first)
        merged = head_n - 1

    demoted = 0
    for m in messages[head_n:]:
        if _is_sys(m):
            dm = dict(m)
            dm["role"] = "user"
            out.append(dm)
            demoted += 1
        else:
            out.append(m)
    return out, merged, demoted


def normalize_messages(params: dict[str, Any]) -> None:
    """就地规范化 params["messages"]。给 _build_litellm_params 收尾用。

    有改动才写回 params, 并记一条 INFO —— 这条日志同时是**证据**:
    数字 > 0 就说明客户端确实在发空 tool_calls; 一直是 0 而 400 还在,
    那就是别的原因, 别在这儿继续找。
    """
    msgs = params.get("messages")
    fixed, n = drop_empty_tool_calls(msgs)
    if n:
        params["messages"] = fixed
        logger.info(
            "message_normalize: 清掉 %d 条 message 上的空 tool_calls/function_call "
            "(dashscope 等上游会因此报 'Empty tool_calls is not supported in message')",
            n,
        )
        msgs = fixed

    fixed2, merged, demoted = collapse_extra_system(msgs)
    if merged or demoted:
        params["messages"] = fixed2
        logger.info(
            "message_normalize: system 归一 —— 开头合并 %d 条, 中间降级成 user %d 条. "
            "内网 Qwen 只认一条 system, 多了返一个 reason/message 全空的 400 "
            "(8/10 打端点逐字复现过)。降级而不是合并到开头, 是因为压缩摘要的位置"
            "有时序意义, 挪到前面就跑到它概括的内容之前了。",
            merged, demoted,
        )
