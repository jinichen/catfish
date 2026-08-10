"""prompt 已经超过模型上下文时, 别发出去 —— 直接告诉员工对话太长 (8/10).

## 现象

员工在一个 700 条的会话里发消息, 界面只显示

    ✗ 未知错误 —— 可以重发一次; 如果一直失败, 把这条截图给 IT

网关日志里是上游返的裸 400 (`reason` / `message` 全空)。为这一个 400 做了两轮
共八个探针都没复现, 最后靠 request_shape_dump 抓到真身, 第一眼就看见:

    "max_tokens": 4096,
    "n_messages": 477,

## 4096 是怎么来的

`_compute_max_allowed_output_tokens`:

    dyn = context_window - prompt_est * 1.3 - safety
    return max(4096, min(dyn, upper))   # ← 注释写着"防压成 0/负数"

这次 prompt_est = 388576, context_window = 128000:

    dyn = 128000 - 505148 - 2048 = **-379196**

不是"稍微紧张", 是 prompt 本身就超了 3 倍。兜底把它变成 4096 **照样发出去**,
上游收到一个装不下的 prompt, 返 400。

**那个"保护"把"对话超长"伪装成了"未知错误"。** 它防住的是 max_tokens 变成负数
(参数非法), 却没防住真正的问题 —— 而且防的方式是让请求继续走, 于是失败发生在
上游、错误信息又是空的, 现场完全看不出原因。

dyn ≤ 0 这个信号本来就已经说明了一切: **装不下**。在这里拦下来, 员工看到的是
一句人话, 而不是 IT 都要查半天的裸 400。

## 为什么放在压缩之后

conversation_compressor 能把 380K 压到 41K (实测省 89%)。压缩之前拦, 会把
本来救得回来的对话也拒掉。所以这道闸只对"压过了还是装不下"的情况开火 ——
调用点在 app.py 压缩之后、发请求之前。

## 阈值取 dyn ≤ 0 而不是 prompt > context

estimate 有 10-25% 误差, 所以 dyn 里已经乘了 1.3 的 buffer。用 dyn ≤ 0 判,
意味着"连 buffer 都吃完了", 比直接比 prompt > context 更保守一点 —— 宁可放过
一个边界情况让上游去判, 也不要把一个其实能跑的请求拦下来。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("catfish.gateway.context_preflight")

#: 超了多少倍就明确说"这个对话太长了"。1.0 以下的措辞会含糊一点。
_HOPELESS_RATIO = 1.5


def format_too_long_message(prompt_est: int, context_window: int, model_name: str) -> str:
    """给员工看的话。**不出现 token / context_window 这些词** —— 员工不需要懂。

    换算成"万字": 中文大致 1 token ≈ 1.5 字, 这里按 1.5 估, 宁可说少不说多。
    """
    approx_wan = prompt_est * 1.5 / 10000
    limit_wan = context_window * 1.5 / 10000
    return (
        f"这个对话太长了 —— 已经积累到大约 {approx_wan:.0f} 万字, "
        f"而模型一次最多能读 {limit_wan:.0f} 万字左右。\n\n"
        f"**开一个新对话就能继续。** 需要的话, 可以先让我把这轮的要点整理出来, "
        f"再带到新对话里。"
    )


def check_context_fits(
    prompt_est: int,
    model,
    *,
    safety: int = 2048,
    buffer_factor: float = 1.3,
) -> str | None:
    """装得下返 None; 装不下返一句给员工看的话 (caller 负责怎么抛)。

    纯函数, 不抛不记日志的那部分好测。真正的日志和 HTTP 由 caller 处理。
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        cw = 0
    if cw <= 0 or prompt_est <= 0:
        # 不知道容量就不拦 —— 让上游去判, 总比拦错强
        return None

    dyn = cw - int(prompt_est * buffer_factor) - safety
    if dyn > 0:
        return None

    name = getattr(model, "name", "?")
    logger.warning(
        "context preflight 拦下: model=%s prompt估算=%d context_window=%d "
        "(dyn=%d ≤ 0, 连 %.0f%% buffer 都吃完了). "
        "**没发给上游** —— 发了也是一个 reason/message 全空的 400, "
        "员工只会看到「未知错误」。压缩已经跑过了 (或在 cooldown 内), "
        "压完仍装不下就该明确告诉员工。",
        name, prompt_est, cw, dyn, (buffer_factor - 1) * 100,
    )
    return format_too_long_message(prompt_est, cw, name)
