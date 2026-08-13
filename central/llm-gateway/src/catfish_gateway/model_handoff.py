"""跨 model 切换的 soft handoff — BL-GATEWAY-SOFT-HANDOFF (5/18 鸿波拍板).

# 背景

Companion / hermes TUI 用户在一个 session 中途切 model (e.g. Nemotron → DeepSeek),
session_id 不变 + transcript 不变 + body["model"] 变了. 当前 gateway 透传, 不做任何
适配. 这在 95% 情况没事, 5% 情况翻车:

  1. 旧 model 是 tool-capable, 新 model 不支持 tools → 历史里的 `tool_calls` /
     `tool_responses` 直接送给新 model, 新 model 见到不认识的 message role
     (e.g. "tool"), 行为不定 (有的 401, 有的幻觉, 有的报错).

# 跟 hermes 0.14 `/handoff` 的关系

hermes 自家 TUI 有 `/handoff` 命令做 4 件事: tool history 兼容 + persona 平滑 +
context 窗口压缩 + memory provider re-bind. **catfish gateway 只做第 1 件**
(P0 实盘踩过), 其余 2 件 hermes 自家会做 / catfish memory 本来就 per-request
不需要 (BL-MEMORY-UNIFIED-INJECT 设计如此), 3 件需求度低跳过.

详见: docs/HERMES-014-AUDIT.md handoff 段, hermes 0.14 #23395.

# Soft handoff 规则 (本模块负责)

触发: request 带 `X-Catfish-Prev-Model: <name>` header + body["model"] != prev_model_name.

动作:
  - new_model.supports_tool_use == False → 把 messages 里所有 tool_calls /
    role="tool" message 转成 inline `<tool_used name="X" args="..." result="..." />`
    嵌进 assistant content. 新 model 当纯文本读.
  - 在 system message 末尾加一行 `[catfish handoff: 上轮 X → 本轮 Y]` 引导新 model
    知道自己是接手, 不要假装"从头开始".

不动:
  - tools array (sanitize_tools 会单独处理)
  - 当前请求 model 路由 (我们不切回 prev_model, 尊重用户选择)
  - persona / 系统 prompt 主体 (Companion 不在切 model 时换 persona)

# 设计原则

  - **Stateless** — gateway 不存 session 上次 model, client 用 header 自报.
    跟 X-Catfish-Teaching-Mode 同模式. 防 gateway 多一份持久化状态.
  - **客户端不传 header → no-op** — 老 hermes / 没改的 Companion 行为不变.
  - **prev == new → no-op** — 同模型继续聊不算 handoff.
  - **新 model 支持 tools → no-op** — 只处理"切到能力弱模型"这一个真坑.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.model_handoff")


def model_supports_tools(model: ModelConfig | None) -> bool:
    """安全获取 model.supports_tool_use。

    8/13 从 tool_capability_guard.py 搬过来 —— 那个模块整套删了 (它的 reroute
    自 5/26 起从没触发过, 详见 app.py 里那段说明), 但**这个函数还活着**:
    apply_soft_handoff 换模型时要判断新模型能不能吃历史里的 tool_calls。
    只剩一个消费方, 所以就地内联, 不为它单留一个模块。

    ⚠ 行为逐字保留, 别"顺手修": `getattr(..., True)` 的 fallback 只在对象**没有
       这个属性**时生效 (例如测试里的 stub)。真的 ModelConfig 一定有这个字段,
       且默认值是 **False** —— 所以对 catalog 里的模型, 漏写 supports_tool_use
       等于声明"不支持"。原 docstring 写的"默认信任"只对 stub 成立, 对真模型是
       反的, 这里改掉那句措辞, 但判断逻辑一个字没动。
    """
    if model is None:
        return True
    return getattr(model, "supports_tool_use", True)


# inline 转译标记 — assistant 当文本读, 不当 tool_call 解析
_TOOL_USED_PREFIX = "[catfish.tool_used]"


def apply_soft_handoff(
    body: dict[str, Any],
    *,
    prev_model_name: str | None,
    new_model: ModelConfig,
    config: Config,  # noqa: ARG001 - reserved for future use (catalog lookup of prev)
) -> tuple[dict[str, Any], str | None]:
    """对 body 应用 soft handoff. 返回 (modified_body, hint).

    Args:
        body: chat completion request body (in-place modified).
        prev_model_name: client 自报的上轮 model 名 (X-Catfish-Prev-Model header).
            None / 空字符串 → 视为同模型, no-op.
        new_model: 本轮 model 解析后的 catalog 配置.
        config: gateway config (reserved, 暂未用).

    Returns:
        (body, hint) — body 是原对象引用 (modified in-place), hint 是 audit log 用的
        简短描述 (or None 表示 no-op).
    """
    # ── No-op 短路 ─────────────────────────────────────────
    if not prev_model_name:
        return body, None
    if prev_model_name == new_model.name:
        return body, None
    if model_supports_tools(new_model):
        # 新 model 支持 tools, 历史 tool_calls 原样发即可
        # (只在 system 末尾加 handoff 提示, 让 LLM 知道身份变了)
        _annotate_handoff(body, prev_model_name, new_model.name)
        return body, f"handoff:{prev_model_name}->{new_model.name}:annotate-only"

    # ── 新 model 不支持 tools → 转 inline summary ─────────────
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return body, None

    transformed, n_calls, n_tools = _transform_tool_messages(messages)
    body["messages"] = transformed

    _annotate_handoff(body, prev_model_name, new_model.name, lossy=True)
    hint = (
        f"handoff:{prev_model_name}->{new_model.name}:"
        f"transformed-{n_calls}-calls-{n_tools}-tool-msgs"
    )
    logger.info("BL-GATEWAY-SOFT-HANDOFF %s", hint)
    return body, hint


def _annotate_handoff(
    body: dict[str, Any], prev_name: str, new_name: str, *, lossy: bool = False
) -> None:
    """在 system message 末尾加 handoff 注记. 没 system 就 prepend 一条."""
    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return
    suffix = (
        f"\n\n[catfish handoff: 上一轮模型 {prev_name}, "
        f"本轮已切到 {new_name}"
    )
    if lossy:
        suffix += "; 历史 tool 调用已转 inline 文本摘要, 新模型不支持 tools"
    suffix += "]"

    # 找第一条 system 把 suffix 追加进去
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str):
                m["content"] = content + suffix
            elif isinstance(content, list):
                # OpenAI 新版 multi-part content. 追加一段 text part.
                content.append({"type": "text", "text": suffix})
            return

    # 没 system → 前面 prepend 一条
    messages.insert(0, {"role": "system", "content": suffix.strip()})


def _transform_tool_messages(
    messages: list[Any],
) -> tuple[list[dict[str, Any]], int, int]:
    """把 messages 里所有 tool_calls / tool 响应转 inline 文本.

    转换规则:
      - assistant 带 tool_calls → tool_calls 字段删掉, 把每个 call summary
        附加到 content 末尾 (前缀 _TOOL_USED_PREFIX 让 audit 能 grep).
      - role="tool" message → 转 role="assistant" 文本 (前面打前缀标识).

    Returns:
        (new_messages, n_tool_calls_unwound, n_tool_msgs_converted)
    """
    n_calls = 0
    n_tools = 0
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue

        role = m.get("role")
        if role == "tool":
            # role="tool" → assistant 文本. content 是 tool result.
            tool_call_id = m.get("tool_call_id", "?")
            tool_name = m.get("name") or m.get("tool_name") or "tool"
            result = m.get("content", "")
            if not isinstance(result, str):
                result = json.dumps(result, ensure_ascii=False)
            out.append({
                "role": "assistant",
                "content": (
                    f"{_TOOL_USED_PREFIX} result tool={tool_name} "
                    f"id={tool_call_id}: {result}"
                ),
            })
            n_tools += 1
            continue

        if role == "assistant" and m.get("tool_calls"):
            # assistant 带 tool_calls → 拆 calls 转 content 摘要
            calls = m.get("tool_calls") or []
            content = m.get("content") or ""
            if not isinstance(content, str):
                # 罕见: content 是 list (multi-part), 转 string
                content = json.dumps(content, ensure_ascii=False)
            summary_lines = []
            for c in calls:
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") or {}
                fn_name = fn.get("name", "?")
                fn_args = fn.get("arguments", "")
                if isinstance(fn_args, (dict, list)):
                    fn_args = json.dumps(fn_args, ensure_ascii=False)
                summary_lines.append(
                    f"{_TOOL_USED_PREFIX} call tool={fn_name} args={fn_args}"
                )
                n_calls += 1
            new_content = content
            if summary_lines:
                if new_content:
                    new_content = new_content + "\n" + "\n".join(summary_lines)
                else:
                    new_content = "\n".join(summary_lines)
            new_m = {k: v for k, v in m.items() if k != "tool_calls"}
            new_m["content"] = new_content
            out.append(new_m)
            continue

        out.append(m)

    return out, n_calls, n_tools
