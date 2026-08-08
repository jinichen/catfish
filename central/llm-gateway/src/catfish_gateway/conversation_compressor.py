"""BL-COMPRESSION-GATEWAY (5/15 早) — 实时压缩长对话历史.

# 问题

Companion 长 session (78+ 条消息) 全量上送 → 95K input/chat, 70K 是历史本身.
跟 BL-LEAN-CHAT (省 inject) 互补 — lean 省 30K inject, compression 省 60-70K history.

# 跟 session_summarizer 区别

| 模块 | 时机 | 目的 | 输出 |
|---|---|---|---|
| session_summarizer | session 结束后 (post-hoc) | 总结进 employee_journal | journal markdown |
| 本模块 (compressor) | 每次 chat 进 gateway 时实时 | 砍中间 N 条压成 1 句, 减 LLM input | 压缩后 messages 数组 |

# 设计

```
触发条件: estimate_tokens(messages) > min(context_window * threshold, 绝对上限)
          比例默认 50%, 绝对上限默认 12 万 (8/8 加, 见 DEFAULT_ABS_TOKEN_CAP)

保护策略:
  - 保留头 N 条 (system + 首条 user, 防丢任务背景)
  - 保留尾 M 条 (最近上下文必须保, LLM 要看)
  - 只压中间段, 替成 1 条 'system: [此前 K 条对话摘要] ...'
  - 头尾两段都清悬挂 tool_calls (8/8 补了头段, 之前只清尾段)

防雪崩:
  - internal call 跳过 (gateway 自己的 loopback, 本来就短)
  - service token 跳过, **但代表某个员工的除外** —— 8/8 修:
    5/19 BL-AUTH-DECOUPLE-A1 之后 hermes-cli 是「service token + X-Catfish-User」,
    员工的主力对话正是从这条路进来的, 老的一刀切豁免让压缩三个月没跑过一次。
    判据在 app.py: effective_user_email != user.sub。
  - 失败 silent (LLM 调挂就用原 messages)
  - 压缩本身 5 分钟 cooldown 同 sub (防短时间多次 chat 反复压)

调 LLM 方式: 走 gateway loopback (跟 session_summarizer 同模式)
  - X-Catfish-Internal: true (跳 quota + 不入 user_day)
  - X-Catfish-Skip-Identity: true (压摘要不要 SOUL 干扰)
  - X-Catfish-Compression-Internal: true (本模块识别, 防 compressor 调自己再触发压缩 = 无限循环)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("catfish.gateway.conversation_compressor")


# ─── 配置 ───────────────────────────────────────────────


#: 默认触发阈值: 估算 token > context * 这比例 才压缩.
#: 50% 是 hermes 0.13 compression.threshold 默认, 跟它对齐.
DEFAULT_THRESHOLD_RATIO = 0.5

#: 触发阈值的**绝对上限** (估算 token). 跟比例阈值取小的那个 (8/8).
#:
#: 只有比例的话, 阈值会跟着 context_window 一起膨胀 —— qwen-flash 标的是
#: 1,000,000, 50% 就是 **50 万**。而痛感根本不在 context 满不满:
#:
#:   鸿波 8/8 实盘 (catfish-public-qwen-flash, 405 条 messages):
#:     prompt_tokens 361,405   离 50 万还差得远, 不触发
#:     ttft_ms       31,394    第一次首字 31 秒
#:     latency_ms    34,099
#:
#: prefill 的代价是按**实际 token 数**付的, 不按"占 context 的百分比"。
#: 上下文越大的模型越不该把阈值放得越松 —— 那正好放跑了最贵的那些请求。
#:
#: 12 万这个数: 估算口径下 (chars/2.5) 约等于 30 万字符, 对应真实 prompt
#: 十几万到二十几万 token 量级 —— 已经是 ttft 明显变差的区间, 而离 1M
#: context 的天花板还很远, 压缩失败也不至于撑爆。
#: 想调不用改代码: env CATFISH_COMPRESSION_MAX_TOKENS。
DEFAULT_ABS_TOKEN_CAP = 120_000
ENV_ABS_TOKEN_CAP = "CATFISH_COMPRESSION_MAX_TOKENS"


def _abs_token_cap() -> int:
    """绝对阈值上限, env 可覆盖。配得不像数字就用默认值, 不因为配错把压缩关掉。"""
    raw = os.environ.get(ENV_ABS_TOKEN_CAP, "").strip()
    if not raw:
        return DEFAULT_ABS_TOKEN_CAP
    try:
        v = int(raw)
    except ValueError:
        logger.warning("%s=%r 不是整数, 用默认 %d", ENV_ABS_TOKEN_CAP, raw, DEFAULT_ABS_TOKEN_CAP)
        return DEFAULT_ABS_TOKEN_CAP
    if v <= 0:
        logger.warning("%s=%d 非正数, 用默认 %d", ENV_ABS_TOKEN_CAP, v, DEFAULT_ABS_TOKEN_CAP)
        return DEFAULT_ABS_TOKEN_CAP
    return v

#: 头部保留多少条 (system + 首条 user 这种背景信息不能丢)
DEFAULT_KEEP_FIRST = 2

#: 尾部保留多少条 (最近上下文, LLM 必须看)
DEFAULT_KEEP_LAST = 8

#: 至少这么多条中间段才值得压 (太少了压完没节省)
MIN_MIDDLE_TO_COMPRESS = 6

#: 同一 sub cooldown 秒数 (防短时间多次压重复花 LLM 钱).
#: 5 分钟 = 用户连续 chat 时只压一次, 之后扩展只 append 不重压.
SUB_COOL_DOWN_SECONDS = 300

#: 压缩 LLM 调用 timeout (秒).
#:
#: 8/8: 30 → 90。30 秒是照"一次小调用"定的, 但压缩自己发的就不是小调用 ——
#: 400 条中间段 × 单条 600 字 cap ≈ 9 万 token 的 prompt, 而它走的是员工同款
#: 模型 (BL-INTERNAL-MODEL-FOLLOW-USER)。同一天的实盘: 那个模型在一个
#: 471 token 的请求上都卡了 60 秒, ttft 一度 31 秒。
#:
#: 30 秒的后果不是"压缩慢", 是**压缩永远失败**: 超时 → _summarize_middle 返
#: None → 打 5 分钟 cooldown → 而且那条日志是 logger.info 级的"跳过", 看起来
#: 跟"不需要压"没区别。修了触发条件却不动这个, 只是从"不压"变成"压不成"。
COMPRESSION_TIMEOUT_SECS = 90.0

#: env override 总开关 (admin 怀疑出问题时可一键关)
ENV_DISABLE = "CATFISH_DISABLE_GATEWAY_COMPRESSION"


# ─── cooldown state ────────────────────────────────────


_SUB_COOL_DOWN: dict[str, float] = {}


def _is_sub_cooling(sub: str) -> bool:
    until = _SUB_COOL_DOWN.get(sub, 0.0)
    return until > time.time()


def _mark_sub_cool(sub: str) -> None:
    _SUB_COOL_DOWN[sub] = time.time() + SUB_COOL_DOWN_SECONDS


# ─── token estimation ─────────────────────────────────


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """估算 messages 数组的 token 数.

    粗算: total chars / 2.5 (中文 1 char ≈ 1 token, 英文 4 chars ≈ 1 token, 取中间).
    精确数靠 tiktoken/jieba 但这里我们只判"该不该压", 粗算够.

    支持:
    - content 是 str: 直接 len
    - content 是 list (multimodal): 累加 text 部分
    - tool_calls: 把每个 call 的 arguments 算上
    """
    total_chars = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or ""
                    if isinstance(text, str):
                        total_chars += len(text)
        # tool_calls: assistant 调工具时 arguments JSON 也算
        tool_calls = m.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") or ""
                    if isinstance(args, str):
                        total_chars += len(args)
        # role/name 也算几字
        total_chars += len(str(m.get("role", ""))) + 4  # 4 ≈ JSON overhead
    return int(total_chars / 2.5)


# ─── 压缩 prompt ───────────────────────────────────────


_COMPRESSION_PROMPT = """你是对话压缩助手. 下面是员工跟鲶鱼的一段中间对话历史.
帮我把它压成一段简短摘要, 让后续对话仍能基于这段背景继续.

# 输出要求

1. 200-400 字 (重要决策不漏, 闲聊砍掉)
2. 第三人称客观陈述 (员工要 X / 鲶鱼答了 Y / 员工决定 Z)
3. 保留: 关键文件路径 / 命令 / API key 占位 / 已做的真操作 / 错误 / 用户偏好
4. 砍掉: 寒暄 / 重复确认 / 工具调用细节 (除非影响决策)
5. 末尾加一句 "继续点: <下一步该做啥>" — 让后续 chat 知道接着干

# 输出格式 (纯文本, 不要 markdown 标题)

员工 [简述上下文]. 关键决策 / 操作:
- ...
- ...

继续点: ...

# 注意

不要总结成 "员工跟鲶鱼讨论了 X" 这种废话, 要写**真做了啥**和**真定了啥**.

# 中间对话历史
"""


# ─── tool-call 边界整理 (BL-COMPRESS-BOUNDARY) ─────────


def _strip_orphan_tool_boundary(
    messages: list[dict[str, Any]],
    cut_idx: int,
) -> list[dict[str, Any]]:
    """从 messages[cut_idx:] 取尾部段, 保证 tool-call 配对完整.

    两类要清的:
      1. 段头 orphan tool: tool message 的父 assistant 在 middle 里被压了 ->
         tool 进 last 段后没爹, Qwen 直 400. 滑切点往后跳掉.
      2. 段尾悬挂 assistant.tool_calls: 最后一个 message 是 assistant 调了
         tool 但 tool reply 没在 segment 里 (被 middle 吞了 / 还没回来) ->
         把 tool_calls 字段去掉但保留 message content.

    设计选择: 不去找匹配 tool_call_id, 只看相邻 role 序列. 因为:
      - hermes-agent 把 tool_calls 和 tool reply 都按时序 append, 紧邻
      - 找 id 要 deep-walk, 代价不值
    """
    n = len(messages)
    # ── (1) 段头 orphan tool 跳过 ──
    cut = max(0, cut_idx)
    while cut < n:
        m = messages[cut]
        if not isinstance(m, dict):
            break
        role = m.get("role")
        if role == "tool":
            # tool 单条 = orphan (父 assistant 在 cut 之前已被压).
            # 也包含 cut 前一条是 assistant.tool_calls 但 keep_first/middle 边界把它切掉的极端情况.
            cut += 1
            continue
        break

    segment = list(messages[cut:n])
    if not segment:
        return segment

    # ── (2) 段尾悬挂 assistant.tool_calls 清掉 ──
    return strip_dangling_tail_tool_calls(segment)


def strip_dangling_tail_tool_calls(
    segment: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """段末尾那条 assistant 若还挂着 tool_calls, 把 tool_calls 去掉。

    它是段的最后一条, 后面不可能有对应的 tool reply —— 对 Qwen / DashScope
    这类严校验的上游就是 400。

    8/8 从 _strip_orphan_tool_boundary 里抽出来: **头段也要过这一遭**。
    原来只有尾段用, 而 maybe_compress_messages 拼的是

        messages[:keep_first] + [摘要] + 尾段
                 ↑ 这一段原样照抄, 没人管它末尾挂没挂 tool_calls

    keep_first 默认 2, 真出现 messages[1] 是带 tool_calls 的 assistant 时,
    它的 tool 回复全被压进摘要里了, 于是**头段末尾留下一个悬挂的 tool_calls**。
    hermes 的 messages[0]/[1] 通常是 system + user 所以现实里没炸过, 但这是
    个真洞 —— 而 8/8 那次 400 (message_normalize.py) 刚证明这类上游有多严。
    """
    if not segment:
        return segment
    last_idx = len(segment) - 1
    last = segment[last_idx]
    if (
        isinstance(last, dict)
        and last.get("role") == "assistant"
        and last.get("tool_calls")
    ):
        out = list(segment)
        new_msg = dict(last)
        new_msg.pop("tool_calls", None)
        # content 空也保留 — 改成空串防 Qwen 嫌空
        if not new_msg.get("content"):
            new_msg["content"] = ""
        out[last_idx] = new_msg
        return out
    return segment


# ─── 压缩主流程 ────────────────────────────────────────


async def maybe_compress_messages(
    messages: list[dict[str, Any]],
    *,
    user_sub: str,
    model_context_window: int,
    threshold_ratio: float = DEFAULT_THRESHOLD_RATIO,
    keep_first: int = DEFAULT_KEEP_FIRST,
    keep_last: int = DEFAULT_KEEP_LAST,
    # BL-INTERNAL-MODEL-FOLLOW-USER (5/17): 严格员工选的 model 同款.
    # caller 必传, None 时跳过 (不 fallback 到别的 model).
    origin_model: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """如果 messages 估算 token > context * threshold, 压中间段.

    Args:
        messages: 当前请求的 messages 数组 (gateway 注入完之后)
        user_sub: 用户 sub (用于 cooldown)
        model_context_window: 目标模型 context window (e.g. 128000)
        threshold_ratio: 触发阈值 (默认 0.5)
        keep_first/keep_last: 头尾保留多少条

    Returns:
        (new_messages, stats) — stats 非 None 表压缩了, 含 pre_token/post_token/compressed_count.
        stats 是 None 表没压 (没达阈值 / cooldown / 失败 / disabled / 中间太少).

    保护:
        - env CATFISH_DISABLE_GATEWAY_COMPRESSION=1 总关
        - cooldown 内同 sub 不重压
        - LLM 调挂 silent 返原 messages
    """
    if os.environ.get(ENV_DISABLE, "").strip() == "1":
        return messages, None

    if not messages or len(messages) <= keep_first + keep_last + MIN_MIDDLE_TO_COMPRESS:
        # 总数不够压, 跳过
        return messages, None

    estimated = estimate_tokens(messages)
    # 8/8: 比例阈值和绝对上限取小的那个。只有比例的话, context_window 越大
    # 阈值越松 (qwen-flash 标 1M → 阈值 50 万), 正好放跑最贵的那批请求。
    cap = min(int(model_context_window * threshold_ratio), _abs_token_cap())
    if estimated <= cap:
        return messages, None

    if _is_sub_cooling(user_sub):
        # 8/8: debug → info。
        # 这条原来是 debug, 生产日志级别是 INFO, 于是"到底为什么没压"在线上
        # 完全不可见 —— 跟"没达阈值"、"压了但没省"长得一模一样。既然走到这里
        # 说明**已经超阈值了**, 那就是一条值得看见的事实。
        logger.info(
            "compression: sub=%s 超阈值但在 cooldown 内, 跳过 (estimated=%d cap=%d, "
            "cooldown %ds)",
            user_sub, estimated, cap, SUB_COOL_DOWN_SECONDS,
        )
        return messages, None

    # 找中间段
    middle = messages[keep_first:len(messages) - keep_last]
    if len(middle) < MIN_MIDDLE_TO_COMPRESS:
        return messages, None

    # 调 LLM 压缩 — 严格员工同款 model (BL-INTERNAL-MODEL-FOLLOW-USER 5/17)
    summary = await _summarize_middle(middle, user_sub=user_sub, origin_model=origin_model)
    if not summary:
        # LLM 挂了不卡主流程, 标 cooldown 防短时间反复重试
        _mark_sub_cool(user_sub)
        return messages, None

    summary_msg = {
        "role": "system",
        "content": f"[此前 {len(middle)} 条对话的压缩摘要 — by catfish-gateway BL-COMPRESSION]\n\n{summary}",
    }

    # ─── BL-COMPRESS-BOUNDARY (5/15 14:11 鸿波撞 Qwen 122B 400) ────
    # 机械切 keep_last 会把 tool 消息切到 assistant_with_tool_calls 之前 —
    # Qwen Go gRPC 校验 "tool 必须紧跟匹配的 assistant.tool_calls", orphan tool 直 400.
    # 修: 把切点往后挪, 直到不是 orphan tool 开头; 同时把尾部悬挂的
    # assistant.tool_calls (对应 tool 已被压缩) 也清掉.
    cut = len(messages) - keep_last
    last_segment = _strip_orphan_tool_boundary(messages, cut)
    # 8/8: 头段也要清悬挂 tool_calls —— 它末尾那条 assistant 的 tool 回复
    # 已经被压进摘要里了。见 strip_dangling_tail_tool_calls 的说明。
    head_segment = strip_dangling_tail_tool_calls(list(messages[:keep_first]))
    new_messages = head_segment + [summary_msg] + last_segment
    post_token = estimate_tokens(new_messages)

    # 压缩本身可能没省 (摘要太长). 真省了再返新版.
    if post_token >= estimated * 0.85:
        logger.info(
            "compression: 摘要没省太多 (%d → %d, < 15%%), 跳过用原 messages",
            estimated, post_token,
        )
        _mark_sub_cool(user_sub)
        return messages, None

    _mark_sub_cool(user_sub)
    return new_messages, {
        "pre_token": estimated,
        "post_token": post_token,
        "compressed_count": len(middle),
        "kept_first": keep_first,
        "kept_last": keep_last,
        "saved_pct": int(100 * (1 - post_token / max(estimated, 1))),
    }


async def _summarize_middle(
    middle_messages: list[dict[str, Any]],
    *,
    user_sub: str,
    origin_model: str | None = None,
) -> str | None:
    """调 gateway loopback LLM 压缩中间段. 失败返 None.

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17 鸿波拍板): 严格用 origin_model 同款.
    None / 不在 catalog / 不可达 → 跳过. 撞错不 fallback.
    """
    # 拼上下文 (限单条最多 600 字, 防中间某 turn 异常长把 prompt 撑爆)
    context_lines = []
    for m in middle_messages:
        role = m.get("role", "?")
        content = m.get("content", "") or ""
        if isinstance(content, list):
            # multimodal: 只取 text 部分
            parts = []
            for p in content:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts.append(p["text"])
            content = "\n".join(parts)
        if not isinstance(content, str):
            content = str(content)
        content = content[:600]  # 单条 cap
        context_lines.append(f"[{role}]: {content}")
    context = "\n\n".join(context_lines)

    user_prompt = _COMPRESSION_PROMPT + context

    # 调 gateway loopback
    try:
        import httpx  # noqa: PLC0415

        from .auth.dev_token import ensure_internal_dev_token  # noqa: PLC0415
        from .config import get_config  # noqa: PLC0415
    except ImportError as e:
        logger.warning("compression: import 依赖失败 (%s), 跳过", e)
        return None

    # BL-INTERNAL-MODEL-FOLLOW-USER (5/17): 严格 origin_model 一个候选
    if not origin_model:
        logger.info(
            "compression: 没 origin_model (caller 没传), 跳过 sub=%s (员工同款规则)",
            user_sub,
        )
        return None

    config = get_config()
    origin_obj = next(
        (m for m in config.models
         if m.name == origin_model and m.mode == "chat" and m.upstream.is_available),
        None,
    )
    if origin_obj is None:
        logger.info(
            "compression: origin_model=%s 不在 catalog / 不可达, 跳过 sub=%s (不 fallback)",
            origin_model, user_sub,
        )
        return None

    port = os.environ.get("PORT", "8999")
    gateway_url = os.environ.get(
        "CATFISH_GATEWAY_INTERNAL_URL",
        f"http://127.0.0.1:{port}/v1/chat/completions",
    )
    dev_token = ensure_internal_dev_token()

    try:
        async with httpx.AsyncClient(timeout=COMPRESSION_TIMEOUT_SECS) as client:
            resp = await client.post(
                gateway_url,
                headers={
                    "Authorization": f"Bearer {dev_token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    # 防自递归 — compressor 调 gateway 不能再触发 compressor
                    "X-Catfish-Compression-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": origin_obj.name,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": 0.2,
                    "max_tokens": 800,
                },
            )
            if resp.status_code != 200:
                logger.info(
                    "compression: %s 返 %d, 跳过 (sub=%s, 不 fallback)",
                    origin_obj.name, resp.status_code, user_sub,
                )
                return None
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            content = choices[0].get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                logger.info(
                    "compression: 压缩成功 sub=%s model=%s (员工同款), summary=%d chars",
                    user_sub, origin_obj.name, len(content),
                )
                return content.strip()
    except Exception as e:
        logger.info(
            "compression: %s 异常 (%s), 跳过 sub=%s",
            origin_obj.name, type(e).__name__, user_sub,
        )
    return None


# ─── header 检测 — 防自递归 ────────────────────────


def is_compression_internal_request(headers: dict | Any) -> bool:
    """检 X-Catfish-Compression-Internal header. 是的话调 compressor 时跳压缩,
    防 compressor 自己调 gateway 再触发压缩 = 无限循环.

    headers 接 dict 或 Starlette Headers 对象.
    """
    if hasattr(headers, "get"):
        return headers.get("X-Catfish-Compression-Internal", "").lower() == "true"
    return False


__all__ = [
    "maybe_compress_messages",
    "estimate_tokens",
    "is_compression_internal_request",
    "_strip_orphan_tool_boundary",
    "strip_dangling_tail_tool_calls",
    "DEFAULT_THRESHOLD_RATIO",
    "DEFAULT_KEEP_FIRST",
    "DEFAULT_KEEP_LAST",
    "DEFAULT_ABS_TOKEN_CAP",
    "ENV_ABS_TOKEN_CAP",
]
