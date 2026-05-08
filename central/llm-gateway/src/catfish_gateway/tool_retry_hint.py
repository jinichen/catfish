"""
tool_retry_hint.py — BL-A1.2 (5/8 ship): LLM 调 tool 连续失败时注入"换思路" hint.

# 为啥要这模块

5/7 鸿波报: "鲶鱼调 tool 失败后 chat 卡死, 不会自己换参数 retry, 跟 Copilot 没区别."

问题来源: LLM 看到 tool 返 error, 默认行为是直接放弃跟员工说"工具不可用". 真 Agent
应该:
  1. 分析 error 为啥失败
  2. 改 args / 换 tool 试一次
  3. 仍失败再换, 3 次后才报员工

# gateway 不直接 retry tool

tool 执行在 client 端 (Companion → tool-bridge). gateway 只看到 messages 历史
(含 tool message 的 ok 字段). 所以这模块**不重新调 tool**, 而是**注入 hint** 给
LLM 看到失败, 主动 retry.

# 检测策略

扫 messages 倒数 N 条, 看 assistant tool_calls 后跟的 tool message:
- 连续 K 次返 error / ok=false → 触发 hint
- hint 注入 system message 给 LLM 下一次调用

# 跟 SOUL.md 纪律的关系

SOUL.md 文档级铁律是软纪律 (LLM 自觉看), gateway hint 是工程级硬注入 (LLM 必看).
两层互补. 软纪律失效 → 工程硬注入兜底.

# 测试

8 单测 (tests/test_tool_retry_hint.py):
    - 没有 tool messages 不注入
    - 1 次 tool 失败不注入 (容忍偶发)
    - 2 次连续相同 tool 同 error → 注入
    - 3 次后注入更强 hint (建议放弃 / 报员工)
    - 不同 tool 失败不连续触发
    - tool 成功后再失败不算连续
    - hint 不重复注入 (already 含 hint 时 skip)
    - inject_tool_retry_hint 不修改原 messages 引用
"""
from __future__ import annotations

import logging

logger = logging.getLogger("catfish.gateway.tool_retry_hint")

# 连续失败几次后开始注入 hint. 1 次容忍偶发, 2 次起开始提醒.
THRESHOLD_HINT = 2
# 多少次后建议放弃 / 报员工 (升级 hint)
THRESHOLD_GIVEUP = 3
# 扫 messages 倒数多少条, 不扫整个历史 (性能 + 准确性, 远古失败不算).
SCAN_TAIL_LEN = 10

# 标志已经注入过 hint 的 marker, 防重复注入
_HINT_MARKER = "[BL-A1.2 tool-retry-hint]"

# 2 次连续失败时的 hint
_HINT_LIGHT = (
    f"{_HINT_MARKER}\n"
    "**注意**: 你上面连续 {n} 次调用 `{tool_name}` 都失败了. 错误信息: \n"
    "{error_summary}\n\n"
    "**不要再用相同的参数调一遍**. 真 Agent 应该:\n"
    "1. 看错误说啥 — 是参数错? 文件不存在? 网络? 权限?\n"
    "2. **改参数 / 换工具** 再试. 例如:\n"
    "   - 文件路径错 → 用 local_search 找对路径\n"
    "   - 网络失败 → 跳过这步, 让员工自己提供数据\n"
    "   - 权限错 → 提示员工授权, 或换个不需要权限的工具\n"
    "3. **仍失败就报员工**, 不要静默卡死."
)

# 3 次后的强 hint
_HINT_STRONG = (
    f"{_HINT_MARKER}\n"
    "⚠️ 你已经连续 {n} 次调用 `{tool_name}` 失败. 错误: \n"
    "{error_summary}\n\n"
    "**立即停止重试**. 不要再调这工具. 现在做:\n"
    "1. 用普通 chat 文字告诉员工\"我尝试了 N 次 X 工具都失败了, 错误是 Y, 你能不能 Z\"\n"
    "2. 提议替代方案 (换工具 / 换路径 / 让员工手动)\n"
    "3. 不要假装成功. **失败要透明, 不要编造结果.**"
)


def _extract_tool_message_status(msg: dict) -> tuple[str | None, str | None]:
    """从一个 'role=tool' 的 message 里取 (tool_name, error_str_if_failed).

    catfish tool-bridge 返的 tool result shape:
        {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

    content 通常是 JSON 字符串 (catfish dispatch 包装的): {"ok": false, "error": "..."}.
    也可能是普通字符串 / 错误的 stringified dict.

    Returns:
        (tool_name, error_str): tool_name 可能为 None (msg 不规范)
                                 error_str 为 None = 没失败
    """
    if not isinstance(msg, dict):
        return None, None
    if msg.get("role") != "tool":
        return None, None
    name = msg.get("name") or msg.get("tool_name")
    content = msg.get("content")

    # content 是 JSON 字符串, 试着解析
    if isinstance(content, str):
        # 简化检测: 字符串包含 '"ok": false' 或 '"error":' (常见 catfish dispatch 失败 shape)
        # 或者 'failed' / 'error' 等词
        lower = content.lower()
        if '"ok": false' in content or '"ok":false' in content:
            # 提取 error 字段 (粗匹配, 不严格 JSON 解析, 容忍 truncated)
            err = _extract_error_from_content(content)
            return name, err
        # 工具自己抛 exception 的格式: 含 'error' / 'exception' / 'failed'
        if any(kw in lower for kw in ("traceback", "exception", "failed:", "error:")):
            return name, content[:200]
        return name, None
    if isinstance(content, dict):
        if content.get("ok") is False:
            return name, str(content.get("error") or content.get("stderr") or "")[:200]
        if content.get("error"):
            return name, str(content["error"])[:200]
        return name, None
    return name, None


def _extract_error_from_content(content: str) -> str:
    """粗暴提取 'error' 字段值, 不依赖严格 JSON parse (防 truncated).

    匹配 '"error": "<value>"' 的 value 字段, 截 200 字.
    """
    import re
    m = re.search(r'"error"\s*:\s*"([^"]{0,500})"', content)
    if m:
        return m.group(1)[:200]
    # fallback: 返前 200 字
    return content[:200]


def _detect_consecutive_failures(messages: list) -> tuple[int, str | None, str]:
    """扫 messages 倒数 SCAN_TAIL_LEN 条, 找最近一组**连续相同 tool** 失败.

    Returns:
        (consecutive_count, tool_name, error_summary)
        consecutive_count = 0 表示没有连续失败
    """
    if not messages:
        return 0, None, ""

    # 倒序扫, 收集最近的 tool 失败链
    tail = messages[-SCAN_TAIL_LEN:]
    failures: list[tuple[str, str]] = []  # [(tool_name, error), ...] 倒序

    for msg in reversed(tail):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "tool":
            name, err = _extract_tool_message_status(msg)
            if err:
                failures.append((name or "<unknown>", err))
            else:
                # 这条 tool 成功了, 中断"连续失败"链
                break
        elif role == "assistant":
            # assistant message 可能含 tool_calls, 跳过 (continue 找 tool result)
            continue
        else:
            # user / system 消息出现, 链断
            break

    if not failures:
        return 0, None, ""

    # 看是否都是同一个 tool
    first_name = failures[0][0]
    same_tool_count = 0
    for name, _err in failures:
        if name == first_name:
            same_tool_count += 1
        else:
            break

    if same_tool_count < THRESHOLD_HINT:
        return 0, None, ""

    # 拼 error_summary (最近几次 error 各一行)
    error_summary = "\n".join(
        f"  尝试 #{i+1}: {err[:150]}"
        for i, (_n, err) in enumerate(failures[:same_tool_count])
    )
    return same_tool_count, first_name, error_summary


def has_existing_hint(messages: list) -> bool:
    """检查 messages 是否已经含 BL-A1.2 hint, 防重复注入.

    BL-FIX6 (5/8): 历史 hint 可能是老版的 role=system, 也可能是新版的 role=user.
    两者都得检测 — 防 BL-FIX6 部署后老 system hint 还在历史里时重复注入.
    """
    for msg in messages[-15:]:  # 倒数 15 条范围检查
        if not isinstance(msg, dict):
            continue
        if msg.get("role") not in ("system", "user"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and _HINT_MARKER in content:
            return True
    return False


def inject_tool_retry_hint(messages: list) -> list:
    """检测 messages 含连续 tool 失败 → 在末尾插入一条 hint.

    返回新 messages 列表, 不修改原引用.
    没触发时返回原 list (不复制).

    BL-FIX6 (5/8): hint 角色从 system 改成 user. 原因 — Qwen Go gRPC adapter 严格
    校验 role 顺序: system 只接受头部 (含连续多条), 中段 (assistant/tool 之后) 出现
    system 撞**空 reason 400**. 改 user 之后跟"员工说一句"等价, 标准 OpenAI 流接受.
    has_existing_hint 也同步扫 user/system 两种 role.
    """
    if not messages:
        return messages

    # 已经注入过 hint, 别重复
    if has_existing_hint(messages):
        return messages

    count, tool_name, error_summary = _detect_consecutive_failures(messages)
    if count < THRESHOLD_HINT:
        return messages

    template = _HINT_STRONG if count >= THRESHOLD_GIVEUP else _HINT_LIGHT
    hint = template.format(
        n=count,
        tool_name=tool_name or "<unknown>",
        error_summary=error_summary,
    )

    new_messages = list(messages)
    # BL-FIX6: role=user, 不是 system. 中段 system 撞 Qwen 400.
    new_messages.append({"role": "user", "content": hint})
    logger.info(
        "tool-retry hint injected (role=user, BL-FIX6): tool=%s consecutive=%d level=%s",
        tool_name, count,
        "STRONG" if count >= THRESHOLD_GIVEUP else "LIGHT",
    )
    return new_messages
