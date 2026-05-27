"""BL-LEARN-RECMODE V2 #68 (5/15, 5/25 搬到 edge/tool-bridge) — selector 漂移自动修复.

# 5/25 BL-RECMODE-MIGRATE-TO-EDGE: 本模块从 central/llm-gateway/.../recmode/ 搬这里.
# 调 aggregator.call_llm (本目录同 module), 不再走中央代码路径.
# gateway /api/learn/repair_selector 现在是 thin proxy.

skill 跑时 catfish_browser_find_by_text(selector_hint) 找不到元素 →
调 catfish-private-main vision fallback 看截图 + 当时语义找新位置 → 返
新 selector 给 skill runtime.

# 触发场景

skill 跑半年后 EIS UI 改版 — 'div.tab-app' 这个录制时 selector 现在
找不到了. 但 selector_hint = {"text": "应用", "near_text": "通讯录"}
里"应用" 仍然在 DOM 里 (只是 div class 名换了). find_by_text 应该
能找到 — 但极少数场景 hint 也漂 (e.g. tab 改名"应用市场"), 这时 main
vision fallback 看截图 + 用户当时语义 ("应用 tab 在通讯录右边") 推
新位置, 返"应用市场" 给 find_by_text 重试.

# 设计

`repair_selector(hint, screenshot_b64, context, gateway_url, auth_token)`:
1. 构造 prompt 给 main: "用户当时找'应用 tab 在通讯录右边', 现在 DOM
   里没'应用'了, 看这截图找候选 (返新 text + near_text + 信心)"
2. main 输出 JSON: {"text": "应用市场", "near_text": "通讯录",
                  "confidence": 0.8, "reason": "..."}
3. caller (catfish_browser_find_by_text) 用新 text 再 find 一次

# v0 局限 (今晚 ship 骨架)

- 不真接 catfish_browser runtime (那要改 tool-bridge dispatch_native, 大改造).
  v0 ship 的是后端 endpoint /api/learn/repair_selector + 纯函数, skill
  runtime 怎么调留 5/26+ 真集成时连
- 写回 skill 文件留 V3 (现在 caller 用一次性返新 selector, 不持久化)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("catfish.recmode.selector_repair")


REPAIR_PROMPT = """你是 catfish-skill-repair. skill 跑时找不到一个 UI 元素,
你看截图 + 当时录制时的语义 hint, 返新 selector 候选给 skill 重试.

# 输入

## 录制时找的 hint
{hint_json}

## 用户录制时的语境 (语音转写 + 上下文)
{context}

## 当前页面截图

(下面 multipart image — 看清楚 + 找候选)

# 输出 schema (严格 JSON)

{{
  "found": true / false,
  "text": "<新 selector text, 给 catfish_browser_find_by_text 用>",
  "near_text": "<旁边文字 disambiguation, 可空>",
  "role": "<button/tab/menuitem/link/...>",
  "confidence": 0.0-1.0,
  "reason": "<为什么挑这个候选, 1 句话>"
}}

# 纪律

- found=false 当且仅当截图里**真没**对应功能元素 (UI 大改 / 走错页)
- 优先匹配语义 (e.g. "应用 tab" → "应用市场" / "应用入口" / "App")
- confidence ≥ 0.7 才返 — 否则 found=false 让 skill 报"DOM 大改, 重录"
- 不要瞎猜 — 不确定就 found=false
"""


def build_repair_messages(
    hint: dict,
    screenshot_b64: str,
    context: str = "",
) -> list[dict]:
    """构造 vision multipart messages 给 main 看 + 推 selector"""
    user_parts: list[dict] = [{
        "type": "text",
        "text": REPAIR_PROMPT.format(
            hint_json=json.dumps(hint, ensure_ascii=False, indent=2),
            context=context or "(无录制时语音上下文)",
        ),
    }]
    if screenshot_b64:
        user_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        })
    return [
        {"role": "system", "content": "你是 catfish-skill-repair, 输出严格 JSON 不要任何 prose."},
        {"role": "user", "content": user_parts},
    ]


def parse_repair_response(raw: str) -> dict:
    """从 main 输出抽 JSON. 跟 aggregator.parse_llm_output 同模式宽容处理.

    Returns: {found, text, near_text, role, confidence, reason}.
        found=False 时其他字段可空.
    """
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        json_str = m.group(1)
    else:
        first = raw.find("{")
        last = raw.rfind("}")
        if first < 0 or last < 0 or last < first:
            return {"found": False, "reason": f"LLM 输出找不到 JSON: {raw[:200]!r}"}
        json_str = raw[first : last + 1]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"found": False, "reason": f"JSON parse 失败: {e}"}
    return {
        "found": bool(data.get("found", False)),
        "text": data.get("text", ""),
        "near_text": data.get("near_text", ""),
        "role": data.get("role", ""),
        "confidence": float(data.get("confidence") or 0.0),
        "reason": data.get("reason", ""),
    }


async def repair_selector(
    hint: dict,
    screenshot_b64: str,
    context: str = "",
    *,
    gateway_url: str | None = None,
    auth_token: str | None = None,
    effective_user: str | None = None,
) -> dict:
    """v0 端到端: hint + 截图 → 调 main → parse → 返新 selector.

    跟 aggregator.call_llm 同模式 — httpx POST /v1/chat/completions, 走自己
    gateway 复用 RBAC + quota. 5/27 BL-RECMODE-AUTH-FORWARD: caller 透
    effective_user (员工 email), call_llm 用它当 X-Catfish-User.
    """
    from . import aggregator  # noqa: PLC0415  复用 call_llm
    messages = build_repair_messages(hint, screenshot_b64, context)
    raw = await aggregator.call_llm(
        messages,
        gateway_url=gateway_url,
        auth_token=auth_token,
        effective_user=effective_user,
        temperature=0.2,  # repair 要稳, 不创造
        max_tokens=500,
    )
    return parse_repair_response(raw)


__all__ = [
    "build_repair_messages",
    "parse_repair_response",
    "repair_selector",
    "REPAIR_PROMPT",
]
