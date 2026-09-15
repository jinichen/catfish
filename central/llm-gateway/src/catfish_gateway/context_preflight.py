"""Context admission shares the same fixed-safety budget as output allocation.

Do not multiply prompt estimates by 1.3: that previously admitted long chats
but clipped every retry to 512 tokens. Semantic compression belongs to Hermes;
the gateway rejects exhausted capacity rather than forwarding a fake budget.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("catfish.gateway.context_preflight")

#: 连这么多 token 的回复都塞不下, 才算真的"装不下"。
#:
#: 512 大约是 700 汉字 —— 够回一句"这个我看了, 结论是…"。低于这个数, 就算
#: 强行发出去, 员工拿到的也是半句话被截断, 体验比明确说"对话太长了"更差。
#:
#: 这个数**故意取小**: preflight 的职责是拦住"铁定不行"的, 不是替员工决定
#: "回复够不够长"。边界情况放过去让上游判 —— 上游真返 400 还有 shape dump
#: 兜着, 而误拦是员工直接用不了。
MIN_USEFUL_OUTPUT = 512


def remaining_output_tokens(prompt_est: int, model) -> int | None:
    """Shared admission/allocation capacity; never invent a positive floor.

    A proportional 1.3 prompt buffer used to erase ~30K of usable output in
    long chats. Use only a fixed, nonnegative reserve on both paths instead.
    CATFISH_PROMPT_BUFFER_FACTOR is intentionally no longer used.
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        return None
    if cw <= 0:
        return None
    try:
        safety = max(0, int(os.environ.get("CATFISH_MAX_TOKENS_SAFETY", "2048")))
    except ValueError:
        safety = 2048
    return max(0, cw - max(0, prompt_est) - safety)


def format_too_long_message(prompt_est: int, context_window: int, model_name: str) -> str:
    """给员工看的话。**不出现 token / context_window 这些词** —— 员工不需要懂。

    换算成"万字": 中文大致 1 token ≈ 1.5 字。

    ⚠ 两个数字接近时不要都四舍五入到整数万 —— 8/10 现场出过
    「已经积累到大约 19 万字, 而模型一次最多能读 19 万字左右」, 读起来就是
    句废话, 员工看了只会觉得系统坏了。差得远就说整数, 差得近就说一位小数。
    """
    approx_wan = prompt_est * 1.5 / 10000
    limit_wan = context_window * 1.5 / 10000
    fmt = "{:.0f}" if abs(approx_wan - limit_wan) >= 1 else "{:.1f}"
    return (
        f"这个对话太长了 —— 已经积累到大约 {fmt.format(approx_wan)} 万字, "
        f"而模型一次最多能读 {fmt.format(limit_wan)} 万字左右。\n\n"
        f"**开一个新对话就能继续。** 需要的话, 可以先让我把这轮的要点整理出来, "
        f"再带到新对话里。"
    )


def check_context_fits(prompt_est: int, model) -> str | None:
    """装得下返 None; 装不下返一句给员工看的话 (caller 负责怎么抛)。

    扣除 prompt 和与输出分配一致的固定安全余量后，至少还能容纳 512。
    不再乘比例 buffer；也不通过输出下限伪造剩余容量。
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        cw = 0
    if cw <= 0 or prompt_est <= 0:
        # 不知道容量就不拦 —— 让上游去判, 总比拦错强
        return None

    remaining = remaining_output_tokens(prompt_est, model)
    if remaining >= MIN_USEFUL_OUTPUT:
        return None

    name = getattr(model, "name", "?")
    logger.warning(
        "context preflight 拦下: model=%s prompt估算=%d context_window=%d "
        "→ 只剩 %d token, 连一句最短回复 (%d) 都塞不下。"
        "**没发给上游** —— 发了也是一个 reason/message 全空的 400, "
        "员工只会看到「未知错误」。语义压缩应由 Hermes 会话层负责。",
        name, prompt_est, cw, remaining, MIN_USEFUL_OUTPUT,
    )
    return format_too_long_message(prompt_est, cw, name)
