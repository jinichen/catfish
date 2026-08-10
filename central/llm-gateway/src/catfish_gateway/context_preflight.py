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

## 判据只看"还剩不剩得下一句回复", **不带 buffer**

⚠ 8/10 第一版拿 `dyn = cw - est*1.3 - safety ≤ 0` 当判据, 并且在这里写着
"比直接比 prompt > context 更保守"。**写反了 —— 乘 1.3 是更激进。** 倒推:

    dyn ≤ 0  ⟺  est ≥ (128000 - 2048) / 1.3 = 96886

也就是 prompt 一过 9.7 万就拦, 而模型装得下 12.8 万 —— **24% 的可用空间被
白白判死**。现场立刻撞上: 一个只有 38 条的会话被拦, 文案自己都荒谬 ——
「已经积累到大约 19 万字, 而模型一次最多能读 19 万字左右」, 两个数一样。

那个 1.3 是给"算 max_tokens 时保守留余量"用的 (宁可少给输出空间也别撑爆),
拿它判"装不装得下"是把两件事混了。边界测试当时也跑出了 `est=97000 → 拦`,
我只验了"边界在 dyn 变号那一刻"就收工, **没问 97000 到底该不该拦**。

现在的判据: `cw - est < MIN_USEFUL_OUTPUT` 才拦 —— 连一句最短的回复都塞不下
才算真装不下。est 用**原始估算**, 不乘 buffer。

宁可放过边界情况让上游去判: 上游真返 400 还有 request_shape_dump 兜着, 而
误拦是员工**直接用不了**。两种代价不对等。
"""

from __future__ import annotations

import logging

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

    判据只有一条: **减掉 prompt 之后, 还剩不剩得下一句最短的回复。**
    不乘 buffer —— 见模块头, 第一版就是拿 max_tokens 那套 1.3 buffer 当判据,
    把 24% 的可用 context 判死了。
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        cw = 0
    if cw <= 0 or prompt_est <= 0:
        # 不知道容量就不拦 —— 让上游去判, 总比拦错强
        return None

    remaining = cw - prompt_est
    if remaining >= MIN_USEFUL_OUTPUT:
        return None

    name = getattr(model, "name", "?")
    logger.warning(
        "context preflight 拦下: model=%s prompt估算=%d context_window=%d "
        "→ 只剩 %d token, 连一句最短回复 (%d) 都塞不下。"
        "**没发给上游** —— 发了也是一个 reason/message 全空的 400, "
        "员工只会看到「未知错误」。压缩已经跑过了 (或在 cooldown 内)。",
        name, prompt_est, cw, remaining, MIN_USEFUL_OUTPUT,
    )
    return format_too_long_message(prompt_est, cw, name)
