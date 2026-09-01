"""LLM 请求参数构造 / 响应解析 —— 从 app.py 拆出 (8/15)。

`chat_completions` / `_stream_chat_completion` / `_invoke_chat_completion` /
`embeddings` 共用的那一层纯计算:

    _resolve_auto_sentinel              catfish-auto → 真模型名
    _build_litellm_params               拼 litellm 的调用参数
    _compute_max_allowed_output_tokens  单次输出上限 (模型 cap ∩ 请求)
    _apply_max_tokens / _apply_prompt_cache_markers
    _extract_nested_usage               从上游响应里挖 usage
    _raise_upstream_error               上游错误 → HTTPException
    _check_context_usage / _model_info_payload

# 为什么这一组能整体搬

  · **零 yield** —— 不像 _stream_chat_completion (它 try 体里 4 处、
    except 里 1 处), 这些都是普通函数, 外提不改变任何控制流语义。
  · 依赖闭合: 它们用到的 6 个 app.py 模块级符号 (AUTO_MODEL_SENTINEL /
    _safe_int_env / _safe_float_env / _provider_supports_cache_marker /
    _http_code_for_upstream / logger) 全库 grep 过, **除本组外没有别的调用方**,
    所以一起搬过来, 这个模块自洽。

# 必须 re-export

tests/test_auto_sentinel.py 是 `from catfish_gateway.app import
AUTO_MODEL_SENTINEL, _resolve_auto_sentinel` 直接拿的。app.py:621 那段注释
定过规矩: 拆出去的东西要在 app.py re-export, 保住这类 import / monkeypatch 路径。
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException

from .errors import friendly_upstream_error as _friendly_upstream_error
from .metrics import log_request_metadata

# 跟 app.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.gateway")

# 6/2 BL-PROMPT-CACHE-PHASE1 (鸿波 6/2 下午拍): provider 真不支持 cache_control 标记
# 的 prefix. LiteLLM 1.86 实测: nvidia_nim / groq 没 cache transform, 标记可能让 NIM
# 严格 schema 校验报 400 BadRequest. 这些 provider 跳过, 0 标记 0 副作用.
#
# 真支持矩阵 (LiteLLM llms/<provider>/chat/transformation.py 含 cache_control 处理):
#   anthropic / dashscope / openrouter / cometapi 真转
#   gemini: 转 cached_content (有 32K 最低 cache size, gemini-3.5-flash; pro 4K)
#   deepseek: openai 协议透传, server 端自动 implicit cache (不依赖客户端标记)
#   私有 vLLM (openai/qwen_*): vLLM prefix cache 自动, 加标记 silently 忽略
#
# 6/2 晚事故经审: 鸿波生产 chat 全挂的真原因是 **hermes API server 8642 CORS 缺
# X-Catfish-* header allowlist**, request preflight 就被浏览器拒, 根本没到 gateway.
# 跟本 cache_control 0 关系. 我曾误"预防性"改 allowlist 收紧, 已 revert. 保持原
# blocklist 模式.
_CACHE_UNSUPPORTED_PROVIDERS = ("nvidia_nim/", "groq/")


def _model_info_payload(m) -> dict[str, Any]:
    """OpenAI-compatible model metadata.

    Clients like Hermes use it to decide context compression, tool support, etc.

    P3.5.13 (6/16 鸿波): 加 'context_length' 字段镜像 context_window, 终结
    hermes 反复探 /api/show 撞 404 的循环.
    真因: hermes agent/model_metadata.py:818 _resolve_endpoint_context_length 用
    matched.get('context_length') (OpenAI / OpenRouter 协议常见命名), 我们返
    context_window / max_context_length 它认不到 → 兜底走 _query_ollama_api_show
    探 Ollama /api/show → 我们不实现 → 404 → 整链路最终 DEFAULT_FALLBACK_CONTEXT,
    没拿到真 ctx 不写 cache (~/.hermes/context_length_cache.yaml). 每次 _create_agent  # noqa: BOUNDARY
    (每次 chat 新建) 都走同一套, log 一直 404.
    加 context_length 后, hermes 步骤 2 拿到真 ctx → save_context_length 写 cache
    → 后续同 model+url 直接从 cache 返 → /api/show 探测整链路 short-circuit.
    """
    return {
        "id": m.name,
        "object": "model",
        "owned_by": "catfish",
        "created": 1700000000,  # static is fine -- OpenAI itself rarely moves this
        # Extended fields many OpenAI-compatible clients inspect:
        "context_window": m.context_window,
        "max_context_length": m.context_window,
        # P3.5.13: hermes / OpenAI / OpenRouter 协议查 context_length, 镜像同值
        "context_length": m.context_window,
        "supports_tool_use": m.supports_tool_use,
        "supports_vision": m.supports_vision,
        "supports_streaming": m.supports_streaming,
        "tier": m.tier,
    }

#: hermes config.yaml `model.default` 的静态占位符 —— **不是**真 model 名。
#:
#: 微信那条路员工没有 picker, hermes 恒发这个名字, gateway 收到后从 roles.yaml
#: 的 chat_default 动态解析成真 model。edge 侧的对应常量在
#: `edge/hermes-plugins/catfish-xcatfish-user/model_authority.py:AUTO_SENTINEL`
#: —— 两边字面量必须一致, test_auto_sentinel.py 里有跨仓一致性测试钉住。
AUTO_MODEL_SENTINEL = "catfish-auto"

def _resolve_auto_sentinel(model_name: str) -> str:
    """`catfish-auto` → roles.yaml chat_default 的真 model 名; 其它名原样返回。

    # 为什么要抽成函数

    8/13 实撞: 这段逻辑原来只写在 `chat_completions` 里 (BL-CATFISH-AUTO-ROUTE),
    于是同一个名字在两个端点上行为不一致 ——

        POST /v1/chat/completions  model=catfish-auto  → 200 (解析成真 model)
        GET  /v1/models/catfish-auto                   → 404

    后果不是"少个端点"这么轻。hermes 的 `agent/model_metadata.py`
    `_query_local_context_length_uncached` 就是靠 `GET /v1/models/{model}` 拿
    context_length 的 (先探 Ollama `/api/show`, 404 后落到这里)。拿不到就走
    `DEFAULT_FALLBACK_CONTEXT = 256_000`, **而且 fallback 结果被有意不写缓存**
    (model_metadata.py:366 的注释), 所以每次 `_create_agent` 都重探一遍。

    实测佐证: `~/.hermes/context_length_cache.yaml` 里每个真 model 名都有条目,  # noqa: BOUNDARY
    唯独 `catfish-auto` 没有。(这里只是引用边缘端文件名作为证据, 中央端不读它 ——
    BL-CENTRAL-EDGE-BOUNDARY 的 noqa 就是给这种文档引用留的。)

    今天 chat_default = catfish-public-deepseek-flash (1M 窗口), 256K 是**低估**,
    只浪费不出错。但这是运气 —— IT 哪天把 chat_default 换成 catfish-private-vision
    (128K), hermes 就会按 256K 往里塞, 超一倍。而 private 是内网模型, 它报错后
    走 fallback chain 就是内网 prompt 出公网。所以这里不能靠"当前配置刚好安全"。

    # 边界

    不做大小写以外的归一化 —— sentinel 是配置文件里写死的字面量, 容忍 typo
    只会让"为什么我的 model 名被换掉了"更难查。

    Raises:
        HTTPException: 500, sentinel 收到了但 roles.yaml 没配 chat_default。
            这是部署错误不是请求错误, 所以是 5xx 不是 4xx。
    """
    if model_name.lower() != AUTO_MODEL_SENTINEL:
        return model_name

    from . import roles as roles_module  # noqa: PLC0415  (延迟 import, 防启动期循环)

    resolved = roles_module.resolve_or_none("chat_default")
    if not resolved:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{AUTO_MODEL_SENTINEL}: roles.yaml chat_default 未配 · "
                "IT 请填 roles.yaml 里 chat_default: <真 model 名>"
            ),
        )
    return resolved

def _safe_int_env(key: str, default: int) -> int:
    """env 读 int, 解析失败回 default. 防 yaml/.env 里值写歪了崩进程."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default

def _safe_float_env(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default

def _compute_max_allowed_output_tokens(
    messages: list, tools: list | None, model
) -> int | None:
    """算当前 model 真实剩余 output 空间. 返 None 表示算不出 (cw=0).

    BL-MAX-TOKENS-DYNAMIC (5/15): cw - prompt_est*buffer - safety. 比硬编码 32K 优:
      - 短 prompt (5-10K) 输出空间 ~120K, 不再被 32K 上限卡住
      - 长 prompt (>96K) 自动留够 prompt 空间, 不会跑 OOM
    BL-TOKEN-COUNTER-LITELLM (5/15 22:00): estimator 走 LiteLLM token_counter, 认
      Llama/Qwen/Gemini/DeepSeek 各家 tokenizer, 准估 ±5%. 准估后 dyn 自然在剩余
      空间内, **不需要硬编码 HARD CAP** (那是偷懒, 鸿波拍过).
    BL-TOOLS-IN-ESTIMATE (5/15 22:00): tools schema 也算 prompt 一部分 (50 tools *
      200 token = 10K, 漏算会撞 ContextWindowExceeded).
    BL-ESTIMATE-ERROR-MARGIN (5/15 22:10): tokenizer 仍有 10-25% 误差 (系统 inject
      markdown / 特殊 token / chat format 差异), 用 buffer_factor (默认 1.3, env
      CATFISH_PROMPT_BUFFER_FACTOR 可调) 给 prompt_est 加成比例 buffer.
    BL-MAX-OUTPUT-TOKENS (5/15 22:31): context_window 跟 max_output_tokens 是俩字段:
      cw = prompt+output 总上限 (DeepSeek 1M); max_out = 单次 output 上限 (DeepSeek
      393K). dyn 必须 clip 到 min(cw, max_out), 否则撞 400 [1, 393216].
    """
    try:
        cw = int(getattr(model, "context_window", 0) or 0)
    except (TypeError, ValueError):
        cw = 0
    if cw <= 0:
        return None
    try:
        from .fallback import estimate_prompt_tokens  # noqa: PLC0415

        prompt_est = estimate_prompt_tokens(
            messages, model=model.upstream.model, tools=tools
        )
    except Exception:  # noqa: BLE001
        prompt_est = 0
    safety = _safe_int_env("CATFISH_MAX_TOKENS_SAFETY", 2048)
    buffer_factor = _safe_float_env("CATFISH_PROMPT_BUFFER_FACTOR", 1.3)
    dyn = cw - int(prompt_est * buffer_factor) - safety
    try:
        max_out = int(getattr(model, "max_output_tokens", 0) or 0)
    except (TypeError, ValueError):
        max_out = 0
    upper = min(cw, max_out) if max_out > 0 else cw
    # ⚠ 8/10: 下限从 4096 改成 MIN_USEFUL_OUTPUT (512)。
    #
    # 原来写 max(4096, ...) 注释是"防压成 0/负数"。防住了参数非法, 却带来一个
    # 更糟的后果: prompt 逼近 context 时 dyn 是负数, 兜底 4096 **照样发出去**,
    # 而 prompt + 4096 已经超了 —— 上游返一个 reason/message 全空的 400,
    # 员工看到「未知错误」。8/10 实测: est=126666 + 4096 = 130762 > 128000。
    #
    # 512 是"还能回一句话"的下限。真到了连 512 都挤不出来的地步, 上游
    # context_preflight 已经在前面拦掉并告诉员工"对话太长了"了, 走不到这里。
    from .context_preflight import MIN_USEFUL_OUTPUT  # noqa: PLC0415

    return max(MIN_USEFUL_OUTPUT, min(dyn, upper))

def _apply_max_tokens(params: dict, model) -> None:
    """In-place 决定 params['max_tokens']:

    - client 没传 → 用 _compute_max_allowed_output_tokens (dyn), 算不出走 32K 兜底
    - client 传了 → 仍 clip 到 dyn (BL-MAX-TOKENS-CLIP 防 fallback 切小 ctx 撞 400)
    """
    allowed = _compute_max_allowed_output_tokens(
        params.get("messages") or [], params.get("tools"), model
    )
    client_val = params.get("max_tokens")
    if "max_tokens" not in params or client_val is None:
        if allowed is not None:
            params["max_tokens"] = allowed
        else:
            params["max_tokens"] = _safe_int_env("CATFISH_MAX_TOKENS_FALLBACK", 32768)
        return
    # client 显式传了 — clip 到 model 真实容量 (BL-MAX-TOKENS-CLIP)
    if allowed is not None and isinstance(client_val, int) and client_val > allowed:
        cw = getattr(model, "context_window", 0) or 0
        logger.warning(
            "BL-MAX-TOKENS-CLIP: client 传 max_tokens=%d 超 model=%s 剩余空间 %d, "
            "clip 到 %d (context_window=%d, prompt 估算占用过大)",
            client_val, model.name, allowed, allowed, cw,
        )
        params["max_tokens"] = allowed

def _provider_supports_cache_marker(upstream_model: str) -> bool:
    """True 时给 system + tools 加 cache_control 标记.

    LiteLLM 转上游时:
    - 支持的 (anthropic/dashscope/gemini): 真省 input tokens 计费
    - 透传不破的 (deepseek/私有 vLLM): silently 忽略, 0 副作用
    - 真破的 (nvidia_nim/groq): 跳过, 防 400 BadRequest
    """
    if not upstream_model:
        return False
    for bad in _CACHE_UNSUPPORTED_PROVIDERS:
        if upstream_model.startswith(bad):
            return False
    return True

def _apply_prompt_cache_markers(params: dict, model) -> None:
    """6/2 BL-PROMPT-CACHE-PHASE1: 给 messages[0] (system) + tools[-1] 加 cache_control.

    Anthropic 风格: 在 system message content list 最后块 + tools 数组最后一个 tool
    上各加一个 cache_control breakpoint. LiteLLM 转给各 provider 原生协议.

    最多 4 个 cache breakpoint (Anthropic 限制), 我们用 2 个 (system / tools), 留
    2 个未来扩展 (user history 长 prompt 时再加).

    幂等: 若 content 已经是 list-of-blocks 且最后块已有 cache_control, 不重复.

    6/2 晚 BL-CACHE-MARKER-EMERGENCY-OFF v2: 真生产 CORS 修通后所有 model
    撞 400 BadRequest, 不只 Gemini. 真原因暂不明 (可能 LiteLLM 1.86 对所有 provider
    都不接 cache_control content list-of-blocks), 暂时**默认关** 防生产挂.
    env CATFISH_CACHE_MARKER_ENABLE=1 显式开 (上线前周一真测过几个 provider 再 toggle).
    """
    if os.environ.get("CATFISH_CACHE_MARKER_ENABLE", "").lower() not in ("1", "true", "yes"):
        return  # 6/2 晚事故: 默认关, 真生产稳定为先
    if not _provider_supports_cache_marker(model.upstream.model):
        return

    messages = params.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    # ── system message: content str → list-of-blocks + cache_control ──
    # 注意 hermes 真生产里第一条总是 system (BL-FIX2 pre-unwrap 也保证), 真实操作上
    # 14.5K 大头都在这条 — 缓存它是收益最大的.
    first = messages[0]
    if first.get("role") == "system":
        content = first.get("content")
        if isinstance(content, str) and content.strip():
            first["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            # 已是 list (vision 或 历史 multipart). 给最后一块 text 加 cache_control.
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "text":
                    if "cache_control" not in block:
                        block["cache_control"] = {"type": "ephemeral"}
                    break

    # ── tools: 最后一个 tool 加 cache_control (Anthropic 风格 — 标 prefix 结尾) ──
    tools = params.get("tools")
    if isinstance(tools, list) and tools:
        last = tools[-1]
        if isinstance(last, dict) and "cache_control" not in last:
            last["cache_control"] = {"type": "ephemeral"}

def _build_litellm_params(body: dict, model) -> dict:
    """Map gateway request -> litellm call params.

    `model.upstream.model` must already contain the LiteLLM provider prefix,
    e.g. 'openai/qwen_v3_5_122b_a10b' or 'gemini/gemini-2.5-pro'.
    """
    params = {k: v for k, v in body.items() if k != "model"}
    params.update(
        {
            "model": model.upstream.model,
            "api_key": model.upstream.api_key,
            # 网络错误快速失败，不走 LiteLLM 默认的重试，避免掩盖真实问题
            "num_retries": 0,
            "timeout": model.upstream.timeout,
        }
    )
    _apply_max_tokens(params, model)
    if model.upstream.api_base:
        params["api_base"] = model.upstream.api_base

    # 6/2 BL-PROMPT-CACHE-PHASE1 (鸿波 6/2 下午拍): 给 system message + tools
    # 加 cache_control. audit 显示真 prompt 34K 里 19K 是 tools + 14.5K 是 system,
    # 99% 是固定开销, 真 user input 占 3%. 上游缓存这部分能省 70-90%.
    #
    # 兼容矩阵 (6/2 audit, LiteLLM 1.86.0):
    #   - deepseek      : 服务端自动 implicit cache, 客户端标记 silently 忽略, 0 副作用
    #   - dashscope     : LiteLLM dashscope transformation 真转 (catfish-public-qwen-flash)
    #   - gemini        : LiteLLM 转 cached_content (gemini-3.5-flash 32K 最低, pro 4K)
    #   - anthropic     : 原生 cache_control 协议 (catfish 现在没用 anthropic 直连)
    #   - 私有 vLLM qwen: vLLM 自动 prefix cache, 标记忽略 (省 GPU 时间不省 token)
    #   - nvidia_nim    : 不支持, 标记可能让 NIM 校验报错 (跳过)
    #   - groq          : 不支持, 标记跳过
    _apply_prompt_cache_markers(params, model)

    # BL-FIX34 (5/10 鸿波诊断): streaming 默认上游不送 usage chunk, gateway
    # 抽 prompt_tokens / completion_tokens 永远 0, audit 写 status=ok tokens=0,
    # quota_events 因 token=0 不记 (record_usage 的 if 条件: tokens>0). 改:
    # streaming 时自动注入 stream_options.include_usage=True, 让 OpenAI 兼容
    # 上游 (deepseek / vLLM v0.5+ Qwen) 在 [DONE] 前送一个 usage chunk.
    # Gemini / Anthropic 的 litellm 包装器 silently ignore 这字段, 无副作用.
    if params.get("stream"):
        so = params.get("stream_options")
        if not isinstance(so, dict):
            so = {}
        if "include_usage" not in so:
            so["include_usage"] = True
        params["stream_options"] = so

    # Apply per-model overrides according to their explicit/request-derived
    # scope. Thinking switches are not universal provider requirements: the
    # legacy-safe ``auto`` policy only applies them to forced tool_choice.
    from .request_param_overrides import applicable_overrides  # noqa: PLC0415

    overrides = applicable_overrides(model.upstream, params.get("tool_choice"))
    if overrides:
        before = {k: params.get(k) for k in overrides}
        params.update(overrides)
        changed = {
            k: {"from": before[k], "to": v}
            for k, v in overrides.items()
            if before[k] != v
        }
        if changed:
            logger.info("applied param_overrides for %s: %s", model.name, changed)
    elif getattr(model.upstream, "param_overrides", None):
        logger.info(
            "skipped request-scoped param_overrides for %s: tool_choice=%r",
            model.name,
            params.get("tool_choice"),
        )

    # 8/8: 出口前最后一道 messages 规范化 —— 删掉"给了但是空的" tool_calls。
    # 放这儿而不是入口: 压缩 / model handoff / lean inject 都在入口之后动
    # messages, 贴着出口做才盖得全。详见 message_normalize.py。
    from .message_normalize import normalize_messages  # noqa: PLC0415

    normalize_messages(params)

    # 8/8: 强制 tool_choice 与深度思考互斥 —— 只关这一次请求的思考。
    # **必须在 param_overrides 之后**, 否则判不出管理员有没有显式配过。
    # 详见 thinking_guard.py (为什么不按模型一刀切关掉)。
    from .thinking_guard import apply as _apply_thinking_guard  # noqa: PLC0415

    _tg = _apply_thinking_guard(params, model)
    if _tg:
        logger.info(
            "thinking_guard: %s 本次强制了 tool_choice (要结构化结果), 关掉深度思考 (%s)。"
            "tool_choice=auto 的普通对话和 agent loop 不受影响 —— 8/10 实测关掉之后"
            "这个模型在 agent loop 里不会收尾, finish_reason 一直是 tool_calls。",
            model.name, _tg,
        )

    return params

def _extract_nested_usage(usage, key: str) -> int:
    """BL-CACHE-AUDIT (5/17): cache_* tokens 可能挂在 usage.prompt_tokens_details
    或 usage 顶 (LiteLLM 不同 provider 不同). 试两个位置都拿不到返 0.
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        v = getattr(details, key, None)
        if v:
            return int(v)
        if isinstance(details, dict):
            v = details.get(key)
            if v:
                return int(v)
    if isinstance(usage, dict):
        v = usage.get(key)
        if v:
            return int(v)
    return 0

def _check_context_usage(model, prompt_tokens: int, user_sub: str) -> None:
    """看本次请求的 prompt_tokens 相对 context_window 的占用。

    两档告警：
        >= 80%  -> WARNING（员工该注意了，建议 /compress 或切 Gemini Pro）
        >= 100% -> ERROR（已经超配，模型可能在跑"侥幸能吃"的路径，下次可能崩）

    不阻断请求，只记日志。实际拒绝交给上游模型自己报 413 / context_length_exceeded。

    建议员工动作（Hermes 0.10 已内置）：
        /compress [focus topic]   手动压缩当前会话，保留指定主题
        /sb                       实时看 ctx 利用率状态栏
        /model <模型名>            切其他模型（Gemini Pro 2M 更适合长 context）
        /new                      开新会话（彻底清空，memory 保留）
    """
    cw = getattr(model, "context_window", 0) or 0
    if cw <= 0 or prompt_tokens <= 0:
        return
    pct = prompt_tokens / cw
    if pct >= 1.0:
        logger.error(
            "context overflow: model=%s prompt_tokens=%d context_window=%d (%.0f%%) "
            "user=%s — 上游可能拒绝。建议员工立刻在 Hermes 里敲 /compress 压缩，"
            "或 /model catfish-public-gemini-pro 切到 2M 上下文模型",
            model.name, prompt_tokens, cw, pct * 100, user_sub,
        )
    elif pct >= 0.8:
        logger.warning(
            "context near limit: model=%s prompt_tokens=%d context_window=%d (%.0f%%) "
            "user=%s — 建议 /compress [当前任务主题] 压缩会话，或 /model 切长上下文模型",
            model.name, prompt_tokens, cw, pct * 100, user_sub,
        )

def _http_code_for_upstream(err_msg: str) -> int:
    """Map an upstream error message to a sensible HTTP status code."""
    low = err_msg.lower()
    if "429" in low or "rate limit" in low or "quota" in low:
        return 429
    if "401" in low or "unauthorized" in low or "invalid api key" in low:
        return 401
    if "timeout" in low or "timed out" in low:
        return 504
    return 502

def _raise_upstream_error(
    exc: Exception,
    *,
    user_sub: str,
    model_name: str,
    latency_ms: float,
    log_context: str,
) -> None:
    """Log metrics + logger.exception + raise HTTPException for upstream errors.

    Factored out because both chat completions (non-stream) and embeddings share
    the exact same error-handling path.

    给客户端的 detail 里同时返回 raw `message` (诊断用) + 翻译过的 `friendly`
    (员工 UI 直接显示给员工看的话), 让 Companion 前端可以二选一.
    """
    err_type = type(exc).__name__
    err_msg = str(exc)[:400]

    # BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt + 内网失败 + 公网被 cap 拦截 →
    # 这是设计行为, 不是上游 bug. 转 503 + 友好消息, 不写 status=error 避免误算.
    from .fallback import LargePromptFallbackBlocked  # noqa: PLC0415
    if isinstance(exc, LargePromptFallbackBlocked):
        log_request_metadata(
            user=user_sub,
            model=model_name,
            latency_ms=latency_ms,
            status="fallback_capped",  # 区别于 'error' 让 audit 看清是 cap 拦的
            error=f"LargePromptFallbackBlocked: prompt={exc.prompt_estimate} cap={exc.cap}",
        )
        logger.warning(
            "BL-FALLBACK-PROMPT-CAP triggered: %s prompt=%d cap=%d primary_err=%s",
            model_name, exc.prompt_estimate, exc.cap, type(exc.last_exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "large_prompt_fallback_blocked",
                "error_type": "LargePromptFallbackBlocked",
                "message": exc.friendly_message(),
                "friendly": exc.friendly_message(),
                "model": model_name,
                "prompt_estimate": exc.prompt_estimate,
                "cap": exc.cap,
                "blocked_chain": exc.blocked_chain,
                "latency_ms": round(latency_ms, 1),
            },
        ) from exc

    log_request_metadata(
        user=user_sub,
        model=model_name,
        latency_ms=latency_ms,
        status="error",
        error=f"{err_type}: {err_msg[:200]}",
    )
    logger.exception("%s [%s @ %.0fms]", log_context, model_name, latency_ms)
    raise HTTPException(
        status_code=_http_code_for_upstream(err_msg),
        detail={
            "error": "upstream error",
            "error_type": err_type,
            "message": err_msg[:300],
            "friendly": _friendly_upstream_error(err_msg),
            "model": model_name,
            "latency_ms": round(latency_ms, 1),
        },
    ) from exc
