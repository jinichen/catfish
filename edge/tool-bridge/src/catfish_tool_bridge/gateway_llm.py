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


def resolve_model(model: str | None = None) -> str:
    """调用方没指定时用哪个模型: Companion 对话选的 (picker) → 中央默认 → 兜底。
    纪要要在 minutes.json 里记下是哪个模型写的 (11/17: 同一份转写 flash 和 MAX 写出来差很多), 所以单独拿出来。"""
    if model:
        return model
    from . import picker_state, role_resolver  # noqa: PLC0415
    return picker_state.read_picker_model() or role_resolver.resolve("chat_default") or "catfish-private-main"


async def call_llm_json(messages: list[dict], parse, **kw):
    """调一次, 按 parse 解析; 解析不了 (模型在 JSON 字符串里写了没转义的双引号之类) 把原样输出和错误给它, 让它改一次。
    11/17 实测千问 4.0 MAX 生成纪要偶发「没按格式输出」, 整个纪要直接失败 —— 再给一次机会。还不行照常抛。"""
    raw = await call_llm(messages, **kw)
    try:
        return parse(raw)
    except ValueError as e:
        logger.info("gateway_llm: 输出不是合法 JSON, 让模型改一次: %s", str(e)[:120])
        fix = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "上面的输出解析不了 (不是合法 JSON, 或者缺了字段)。只输出改好的那一个 JSON 对象, 不要别的文字; "
                                        "字符串里要引用原话用「」, 不要用没转义的英文双引号。"},
        ]
        return parse(await call_llm(fix, **kw))


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

    model = resolve_model(model)

    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    # 11/3: 这里只发单轮、不带工具的请求 (会议纪要等), 不要深度思考 —— 网关给 qwen3.8 配的
    # 「关思考」只在强制调工具时生效, 不带这个头就开着, 长会议一次要 2–3 分钟, 容易撞网关 180 秒超时。
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "X-Catfish-Thinking": "off"}
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
