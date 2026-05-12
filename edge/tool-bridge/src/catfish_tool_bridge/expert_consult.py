"""BL-FED2.3 (5/12 鸿波拍板) — 跨员工路由 catfish_expert_consult.

# 真问题

BL-FED2.1 把员工专长抽到 yaml, BL-FED2.2 暴露黄页 endpoint. 但**员工在 Companion
里问"谁懂资质?"时**, LLM 现在还得手工:
  1. 调 catfish_list_expertise (自己机器, 答非所问)
  2. 或者催员工自己去问黄页 endpoint

太麻烦. 这一层就是**自动路由**: 员工问 "X 怎么处理?" → LLM 抽 tag → 一次
catfish_expert_consult 搞定 (黄页 + A2A 委托 + 友好回答).

# 调用链路

```
employee A's Companion 问 "资质审核怎么搞?"
  → LLM 抽 tag="资质审核"
  → catfish_expert_consult(expertise_tag="资质审核", question="资质审核怎么搞?")
       1. 调 /registry/by-expertise?tag=资质审核
          → matches: [Bob (在线, expertise=[资质审核, 财务]), Charlie (离线)]
       2. 选 Bob (排除 A 自己, 优先在线)
       3. 调 gateway /a2a/internal/ask → Bob 机器 LLM 流式回答
       4. 返 answer + routed_to=Bob
```

# 跟 catfish_a2a_ask 的关系

catfish_a2a_ask 还是底层 primitive — 给定 to_sub, 调对方鲶鱼. **expert_consult
是上一层路由**: 给定 tag, 自动选到底找谁. 重叠的 question/purpose/context_hint
参数透传.

# 隐私边界

- 我方只把 expertise_tag 上行查中央 (大小写不敏感, BL-FED2.2 已做脱敏)
- 中央返的 matches 不含 jwks_uri / public_pem / catfish_endpoint
- 实际 A2A 调用走 gateway /a2a/internal/ask, 复用 ALLOW.md 拦截 (对方机器决定
  答不答 — BL-FED2.2 design 里说 "调用前给被咨询员工显式提示". 当前 P1 待办,
  ALLOW.md 软策略已生效, Companion 实时弹窗是 BL-FED2.4 工作)
- from_sub == to_sub 时跳过 (不问自己)

# 错误处理

| 场景 | 返 |
|---|---|
| 黄页查不到 | ok=False, 友好提示"没人注册黄页" |
| 全部离线 (不指定 preferred) | ok=False, 列 sub + last_seen, 建议晚点再问 |
| preferred_sub 不在黄页 | ok=False, 列候选 |
| a2a denied (ALLOW.md 拒) | ok=False, 透传拒答理由 |
| gateway /a2a 调用失败 | ok=False, 透传错误 |
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger("catfish.tool_bridge.expert_consult")


# ── 中央 registry endpoint (BL-FED2.2 黄页) ─────────────────────

def _registry_url() -> str:
    return os.environ.get("CATFISH_REGISTRY_URL", "http://127.0.0.1:8998").rstrip("/")


def _gateway_url() -> str:
    return os.environ.get("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999").rstrip("/")


def _from_sub() -> str:
    return os.environ.get("CATFISH_USER_SUB", "").strip()


# ── 低层 HTTP 调用 (注入式 — 便于测试 mock) ─────────────────────


def _http_get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """GET 返 JSON. 失败抛 RuntimeError (调用方接)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason} {url}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"GET 失败 {url}: {e}") from e


def _http_post_json(url: str, body: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason} {url}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"POST 失败 {url}: {e}") from e


# ── 中层: 黄页查 + A2A 调 ──────────────────────────────────────


def query_by_expertise(
    tag: str,
    online_only: bool = False,
    http_get: Callable[[str], dict[str, Any]] = _http_get_json,
) -> dict[str, Any]:
    """调中央 /registry/by-expertise. 返 raw response (matched_count + matches)."""
    if not tag or not tag.strip():
        raise ValueError("tag 必填")
    online_qs = "&online_only=true" if online_only else ""
    # tag 自带 URL-encode
    import urllib.parse  # noqa: PLC0415
    url = (
        f"{_registry_url()}/registry/by-expertise?"
        f"tag={urllib.parse.quote(tag.strip())}{online_qs}"
    )
    return http_get(url)


def call_a2a_ask(
    to_sub: str,
    question: str,
    purpose: str = "",
    context_hint: str = "",
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] = _http_post_json,
) -> dict[str, Any]:
    """调本机 gateway /a2a/internal/ask. 返 raw response."""
    from_sub = _from_sub()
    if not from_sub:
        return {"ok": False, "error_type": "config", "error": "CATFISH_USER_SUB env 未设"}
    body = {
        "from_sub": from_sub,
        "to_sub": to_sub,
        "question": question,
        "purpose": purpose,
        "context_hint": context_hint,
    }
    url = f"{_gateway_url()}/a2a/internal/ask"
    try:
        return http_post(url, body)
    except RuntimeError as e:
        return {"ok": False, "error_type": "transport", "error": str(e)}


# ── 顶层: 路由策略 ─────────────────────────────────────────────


def _select_target(
    matches: list[dict[str, Any]],
    preferred_sub: str,
    from_sub: str,
) -> tuple[Optional[dict[str, Any]], str]:
    """从 matches 选实际要问的员工.

    返 (chosen_match, reason). chosen_match=None 表示选不到.
    """
    if not matches:
        return None, "黄页空"

    # 排除自己 — 员工不该被路由到自己
    valid = [m for m in matches if m.get("sub") and m["sub"] != from_sub]
    if not valid:
        return None, f"黄页里只有你自己 ({from_sub}), 没别人懂这个 tag"

    if preferred_sub:
        # 指定问谁 — 必须在 matches 里
        for m in valid:
            if m["sub"] == preferred_sub:
                if not m.get("online"):
                    return m, f"⚠️ {preferred_sub} 当前离线 (last_seen={m.get('last_seen','?')}), 仍尝试转发"
                return m, f"按 preferred_sub 路由到 {preferred_sub}"
        return None, (
            f"preferred_sub '{preferred_sub}' 不在懂这个 tag 的同事里. "
            f"候选: {[m['sub'] for m in valid]}"
        )

    # 自动路由: 优先在线 (matches 已按 BL-FED2.2 endpoint 排序: online 优先,
    # last_seen 降序), 这里只挑第一个在线的
    online = [m for m in valid if m.get("online")]
    if online:
        return online[0], f"自动路由到在线员工 {online[0]['sub']}"
    # 全离线 — 不主动联系 (除非显式 preferred). 让 LLM 知会员工换时间问.
    return None, (
        f"懂这个 tag 的同事 {[m['sub'] for m in valid]} 都不在线. "
        "建议晚点再问, 或传 preferred_sub 强制转发 (对方可能也不会立刻回)."
    )


def tool_expert_consult(
    args: dict[str, Any],
    *,
    http_get: Callable[[str], dict[str, Any]] = _http_get_json,
    http_post: Callable[[str, dict[str, Any]], dict[str, Any]] = _http_post_json,
) -> dict[str, Any]:
    """catfish_expert_consult 入口.

    args:
      expertise_tag: str (必填) — 想问的领域 tag
      question: str (必填) — 具体问题
      preferred_sub: str (可选) — 指定问谁
      purpose: str (可选) — 用途分类 (透传 a2a, ALLOW.md 用)
      context_hint: str (可选) — 背景说明 (透传 a2a)
    """
    expertise_tag = (args.get("expertise_tag") or "").strip()
    question = (args.get("question") or "").strip()
    preferred_sub = (args.get("preferred_sub") or "").strip()
    purpose = (args.get("purpose") or "").strip()
    context_hint = (args.get("context_hint") or "").strip()

    if not expertise_tag:
        return {"ok": False, "error": "expertise_tag 必填 — 你要找懂什么的同事?"}
    if not question:
        return {"ok": False, "error": "question 必填 — 具体问题是什么?"}

    from_sub = _from_sub()
    if not from_sub:
        return {
            "ok": False,
            "error": (
                "CATFISH_USER_SUB env 未设, 单机 mock 必须设 (生产从 SSO 拿). "
                "没有 from_sub 中央 registry 也无法签 A2A token."
            ),
        }

    # 1. 黄页查
    try:
        resp = query_by_expertise(expertise_tag, online_only=False, http_get=http_get)
    except RuntimeError as e:
        return {
            "ok": False,
            "error": f"查中央黄页失败: {e}. 检查 CATFISH_REGISTRY_URL + identity-server 是否起.",
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    matches = resp.get("matches") or []
    matched_count = resp.get("matched_count", len(matches))
    online_count = resp.get("online_count", sum(1 for m in matches if m.get("online")))

    if matched_count == 0:
        return {
            "ok": False,
            "error": (
                f"黄页里**没人**注册 '{expertise_tag}' 这个专长. "
                "可能原因:\n"
                "  - 没人懂这个领域 (问问员工的同事吧)\n"
                "  - 懂的同事还没在 mac 上跑 catfish_extract_expertise + catfish_confirm_expertise\n"
                "  - 或他们 confirm 后 gateway 还没 self_register 上去 (心跳 60s 一周期)"
            ),
            "expertise_tag": expertise_tag,
            "matched_count": 0,
        }

    # 2. 选目标
    chosen, reason = _select_target(matches, preferred_sub, from_sub)
    if chosen is None:
        return {
            "ok": False,
            "error": reason,
            "expertise_tag": expertise_tag,
            "matched_count": matched_count,
            "online_count": online_count,
            "candidates": [m["sub"] for m in matches if m.get("sub") != from_sub],
        }

    # 3. 调 a2a_ask
    a2a_resp = call_a2a_ask(
        to_sub=chosen["sub"],
        question=question,
        purpose=purpose or f"expert_consult:{expertise_tag}",
        context_hint=context_hint,
        http_post=http_post,
    )

    if not a2a_resp.get("ok"):
        err_type = a2a_resp.get("error_type", "")
        err_msg = a2a_resp.get("error", "")
        if err_type == "denied":
            return {
                "ok": False,
                "error": (
                    f"{chosen['sub']} 的鲶鱼按 ALLOW.md 拒绝了这个问题: {err_msg}. "
                    f"建议换个角度问, 或问员工本人有没有授权 expertise='{expertise_tag}' 的咨询."
                ),
                "expertise_tag": expertise_tag,
                "routed_to": chosen["sub"],
                "routing_reason": reason,
            }
        return {
            "ok": False,
            "error": f"转发到 {chosen['sub']} 失败 ({err_type}): {err_msg}",
            "expertise_tag": expertise_tag,
            "routed_to": chosen["sub"],
            "routing_reason": reason,
        }

    return {
        "ok": True,
        "expertise_tag": expertise_tag,
        "routed_to": chosen["sub"],
        "routing_reason": reason,
        "routed_department": chosen.get("department", ""),
        "answer": a2a_resp.get("answer", ""),
        "chunks_count": a2a_resp.get("chunks_count", 0),
        "matched_count": matched_count,
        "online_count": online_count,
        "summary": (
            f"📞 转给 {chosen['sub']} ({chosen.get('department','?')}) — "
            f"懂 '{expertise_tag}' 的 {matched_count} 个同事里在线 {online_count} 个, "
            f"答案 {a2a_resp.get('chunks_count', 0)} 个 chunk. 详见 answer 字段."
        ),
    }


__all__ = [
    "query_by_expertise",
    "call_a2a_ask",
    "tool_expert_consult",
    "_select_target",
]
