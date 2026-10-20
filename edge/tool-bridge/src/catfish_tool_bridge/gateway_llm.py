"""调本机网关 /v1/chat/completions 的异步小函数 —— 会议纪要在用。

10/20: 原来住在 recmode/aggregator.py (录屏学习的综合那步), 会议纪要 (10/1) 借它
调大模型。录屏学习整体拿掉后单独留下这一个函数, 行为不变:

- 走自己的 gateway (复用 RBAC / quota / fallback), 不直连 LiteLLM
- model 不传 → 员工临时切的模型 > role_resolver 的 chat_default > catfish-private-main
- 不继承员工 shell 里的代理 (trust_env=False): Clash / PAC / 坏掉的 NO_PROXY 会把
  本机请求送错出口, 或在发请求前就抛 ProxyError
- service token 模式下 gateway 要求 X-Catfish-User, caller 透 effective_user 下来
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("catfish.tool_bridge.gateway_llm")


async def call_llm(
    messages: list[dict],
    model: str | None = None,
    *,
    source: str,
    gateway_url: str | None = None,
    auth_token: str | None = None,
    effective_user: str | None = None,
    timeout_s: float = 300.0,
    temperature: float = 0.3,
    max_tokens: int = 8000,
) -> str:
    """发一次非流式 chat, 返回 assistant 文本。

    Args:
        messages: OpenAI 格式 messages
        model: None → picker_state > role_resolver("chat_default") > catfish-private-main
        source: 网关归属标记 (catfish_memory_gateway.with_source), 例 plugin:toolbridge-meeting
        gateway_url: 默认 env CATFISH_GATEWAY_URL 或 http://localhost:8999
        auth_token: 默认 env CATFISH_DEV_TOKEN

    Raises:
        RuntimeError: 没 token / gateway 不可达 / 非 200 / 空回复
    """
    import httpx  # noqa: PLC0415

    gw = gateway_url or os.environ.get("CATFISH_GATEWAY_URL", "http://localhost:8999")
    tok = auth_token or os.environ.get("CATFISH_DEV_TOKEN", "")
    if not tok:
        raise RuntimeError("gateway_llm.call_llm: 没 auth token. 设 CATFISH_DEV_TOKEN env 或 caller 显式传 auth_token.")

    if model is None:
        from . import picker_state, role_resolver  # noqa: PLC0415
        model = picker_state.read_picker_model() or role_resolver.resolve("chat_default") or "catfish-private-main"

    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    if effective_user:
        headers["X-Catfish-User"] = effective_user.strip()

    logger.info("gateway_llm: 发 %d messages → %s (%s)", len(messages), gw, source)
    async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
        try:
            resp = await client.post(f"{gw}/v1/chat/completions?catfish_source={source}",
                                     json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise RuntimeError(f"gateway {gw} 不可达 ({e}). 看 catfish-gateway 起没.") from e

        if resp.status_code != 200:
            # 把 gateway 友好错误透传 (e.g. 503 LargePromptFallbackBlocked / 429 quota)
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise RuntimeError(f"gateway 返 {resp.status_code}: {detail}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM 没返 choices: {data}")
        content = (choices[0].get("message") or {}).get("content", "")
        if not content:
            raise RuntimeError(f"LLM 返空 content (可能被 max_tokens 截): {data}")
        usage = data.get("usage") or {}
        logger.info("gateway_llm: done. prompt=%d completion=%d",
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return content
