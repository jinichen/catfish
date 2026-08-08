"""模型 fallback 链 —— 上游 429/503/504/timeout 时自动切下一个模型。

设计目标:
    1. 员工看到的是"模型答了我"或"全 chain 挂了"两个状态, 中间切换无感
    2. 不破坏现有 invoke 路径 —— 给 chat_completions 一个包装函数即可
    3. fallback 决策**只看错误类型**, 不看 prompt 内容 → 简单可预测
    4. max_hops 兜底防 chain 互相循环 (a→b→a→b...)

支持的错误判定:
    HTTP 状态码: 429 / 503 / 504
    异常字符串包含: "timeout" / "rate limit" / "quota" / "RESOURCE_EXHAUSTED"

不支持的:
    400 (客户端错) / 401 (鉴权错) / 422 (schema 错) → 切模型不会修, 直接报错

时序:
    1. 调主模型 → 异常 e
    2. should_fallback(e, model.fallback.on_errors) → True
    3. 取 chain[0], 验证它能用 (api_key_configured), 不能就跳到 chain[1]
    4. 重新调 _build_litellm_params + acompletion
    5. 重复直到成功 或 hops > max_hops 或 chain 空
    6. 全失败时: 抛最后一个错误 (而不是第一个 —— 最新错误更有诊断价值)

注: 流式响应的 fallback 比非流式难 —— 流式可能在中途挂 (response 已发了一半 chunk),
   这种情况无法干净切换。所以**首字之前的错误**才走 fallback, 已开始流就让它挂。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .config import Config, ModelConfig

logger = logging.getLogger("catfish.gateway.fallback")


# ============================================================
# 错误判定
# ============================================================

# 字符串关键词 —— 不区分大小写匹配错误的 str(exc)
#
# 注意: "connection error" / "connection refused" / "broken pipe" 这三类要触发
#       fallback, 因为内网 (10.10.40.x) VPN 抖动 / 平台重启时这就是 LiteLLM 抛出来的。
#       Without 这些, 内网 vision 挂掉时 fallback chain 不会启动, 员工就看到一脸懵的
#       "InternalServerError: Connection error" — 落不到公共模型。
_ERROR_KEYWORDS = {
    "timeout": ("timeout", "timed out"),
    "rate limit": ("rate limit", "ratelimit", "quota", "resource_exhausted"),
    "connection error": (
        "connection error",
        "connection refused",
        "cannot connect",
        "connect call failed",
        "broken pipe",
        "apiconnectionerror",
    ),
    "connection refused": ("connection refused", "connect call failed"),
    # 5/7 鸿波微信演 demo 时撞: dashscope qwen "free tier only" 模式配额烧光,
    # 抛 AllocationQuota.FreeTierOnly + APIError 不在 fallback 列表 → fallback 链停.
    # 加进 "free tier" 关键字让 fallback 链跳到 deepseek / gemini 兜底.
    "free tier": (
        "free tier",
        "free tier of the model has been exhausted",
        "allocationquota.freetieronly",
        "freetieronly",
    ),
    # 5/7 同样 dashscope upstream 502 时 catfish-public-qwen-flash 报 "upstream error"
    # 也应该跳到下一个 candidate (deepseek-flash / gemini-flash)
    "upstream error": ("upstream error", "upstream timeout"),
    # 8/8 鸿波实撞: DeepSeek 余额烧光, 早安页和邮件评级全挂, **而且没有 fallback**:
    #
    #   Client error '402 Payment Required' for url 'https://api.deepseek.com/...'
    #   litellm.BadRequestError: DeepseekException -
    #       {"error":{"message":"Insufficient Balance","code":"invalid_request_error"}}
    #   catfish.gateway.fallback: err=BadRequestError 不在 on_errors 里, 不 fallback
    #
    # 两道都没接住:
    #   · status: LiteLLM 把 402 重映射成 BadRequestError, _extract_status_code
    #     拿到的是 **400**, 402 根本不出现在异常上
    #   · 关键词: "rate limit" 组里有 "quota", 但报文写的是 "Insufficient Balance",
    #     一个字都不沾
    #
    # 而余额不足**恰恰是最该切模型的情况** —— 换一家立刻能用, 不切就是全线停摆。
    # 它被判成"客户端请求错误"纯粹是上游的分类问题, 跟请求本身没关系。
    #
    # 各家的说法不一样, 一起收进来 (都是实际见过或文档里的原话):
    #   DeepSeek  Insufficient Balance / 402 Payment Required
    #   OpenAI    insufficient_quota
    #   阿里云百炼  Arrearage (欠费) / AllocationQuota
    # 不收 "billing" 这种太宽的词 —— 正常文案里也可能出现, 误切比不切更难查。
    "insufficient balance": (
        "insufficient balance",
        "insufficient_balance",
        "insufficient quota",
        "insufficient_quota",
        "payment required",
        "arrearage",
        "余额不足",
        "欠费",
    ),
}


def should_fallback(exc: Exception, on_errors: Iterable) -> bool:
    """判断这个异常是否应该触发 fallback。

    on_errors 里的元素可以是:
        - int (HTTP status code, 例如 429)
        - str (关键词, 例如 "timeout"; 大小写不敏感, 子串匹配)
    """
    msg = str(exc).lower()
    status = _extract_status_code(exc)

    for trigger in on_errors:
        if isinstance(trigger, int):
            if status == trigger:
                return True
            # 也检查错误消息里的状态码字面 (LiteLLM 经常把 status 嵌入消息)
            if f" {trigger} " in f" {msg} " or f"{trigger}:" in msg:
                return True
        elif isinstance(trigger, str):
            key = trigger.lower()
            # 直接子串匹配
            if key in msg:
                return True
            # 别名扩展 (timeout 也匹配 "timed out" 等)
            if key in _ERROR_KEYWORDS:
                if any(kw in msg for kw in _ERROR_KEYWORDS[key]):
                    return True
    return False


class LargePromptFallbackBlocked(Exception):
    """BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt + 内网失败 + 全公网被 cap 过滤 → 抛这个.

    caller (chat_completions) 捕获后转友好 503: "内网暂时不可达 + 你的请求 ~67K 超
    公网 fallback cap 30K, 公网更慢更贵不切. 1 分钟后重试或换大 context 模型."
    """

    def __init__(
        self,
        primary_name: str,
        prompt_estimate: int,
        cap: int,
        last_exc: Exception,
        blocked_chain: list[str],
    ):
        self.primary_name = primary_name
        self.prompt_estimate = prompt_estimate
        self.cap = cap
        self.last_exc = last_exc
        self.blocked_chain = blocked_chain
        super().__init__(self.friendly_message())

    def friendly_message(self) -> str:
        return (
            f"内网 LLM ({self.primary_name}) 暂时不可达 ({type(self.last_exc).__name__}: "
            f"{str(self.last_exc)[:120]}), 你的请求 ~{self.prompt_estimate:,} tokens 超过"
            f"公网 fallback 上限 {self.cap:,} (公网更慢更贵, 大 prompt 不切公网). "
            f"建议: (1) 1-2 分钟后重试内网 (2) 主动切到 catfish-public-gemini-pro (2M context, "
            f"自己选这个就接受公网) (3) 减少 prompt (如清空对话历史 / 不带附件). "
            f"被跳过的公网 chain: {self.blocked_chain}"
        )


def estimate_prompt_tokens(
    messages: list[dict] | None,
    model: str | None = None,
    tools: list[dict] | None = None,
) -> int:
    """估 prompt tokens. 给 fallback cap + dynamic max_tokens 用.

    历史演进:
      char/2 (5/14): Llama 中文低估 76% (39K 估 / 69K 真)
      LiteLLM token_counter (5/15 鸿波 '硬编码偷懒'): 准估 messages, Llama 39K → 42K
        但仍低估 40%, 因为 LiteLLM `token_counter(messages)` 只算 messages,
        不算 chat completion body 的 `tools` field. 上游 (OpenAI/NVIDIA 等)
        算 prompt 时把 tools schema 也当 input — 这是漏估的根本原因.
      BL-TOOLS-IN-ESTIMATE (5/15 22:00 鸿波 'NVIDIA 还撞 ContextWindowExceeded'):
        把 tools schema 也算进 prompt 估算. 50 个 tool * 平均 200 token/tool ≈ 10K.

    Args:
        messages: chat messages 数组 (含 content / tool_calls / multipart)
        model:    LiteLLM 完整 model 名. None → 用 char/2 兜底.
        tools:    OpenAI 风格 tools 数组 (function schemas). 加进 prompt 估算.

    image_url 仍不算 (固定开销, vision 模型 token 由上游真实算).
    """
    if not messages and not tools:
        return 0

    # ── messages 部分 ──
    messages_tokens = 0
    if messages:
        if model:
            try:
                import litellm  # noqa: PLC0415

                count = litellm.token_counter(model=model, messages=messages)
                if isinstance(count, int) and count > 0:
                    messages_tokens = count
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "estimate_prompt_tokens: LiteLLM token_counter model=%s 失败 (%s), "
                    "fallback char/2 估算",
                    model, e,
                )

        if messages_tokens == 0:
            # Fallback: char/2 粗估
            total_chars = 0
            for m in messages:
                c = m.get("content")
                if isinstance(c, str):
                    total_chars += len(c)
                elif isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "text":
                            total_chars += len(part.get("text", ""))
                tcs = m.get("tool_calls") or []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        total_chars += len(args)
            messages_tokens = total_chars // 2

    # ── tools 部分 (BL-TOOLS-IN-ESTIMATE) ──
    # 上游算 prompt 时把 tools schema 也算 input. 50 tools * 200 token ≈ 10K.
    # tools schema 是 JSON, 偏 4 char/token (相对 char/2 中英混更准).
    # 不调 LiteLLM token_counter 因为它不接 tools, 自己算字符数即可.
    tools_tokens = 0
    if tools:
        try:
            import json as _json  # noqa: PLC0415

            tools_json = _json.dumps(tools, ensure_ascii=False)
            tools_tokens = len(tools_json) // 4  # JSON ~4 char/token
        except Exception as e:  # noqa: BLE001
            logger.debug("estimate_prompt_tokens: tools 序列化失败 (%s), 跳过", e)

    return messages_tokens + tools_tokens


def _extract_status_code(exc: Exception) -> int | None:
    """从 LiteLLM / httpx / generic 异常里提取 HTTP status code。

    LiteLLM 的 RateLimitError 等子类有 .status_code, 但有时只是嵌入消息。
    """
    for attr in ("status_code", "http_status", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    # response 属性 (httpx style)
    resp = getattr(exc, "response", None)
    if resp is not None:
        v = getattr(resp, "status_code", None)
        if isinstance(v, int):
            return v
    return None


# ============================================================
# Fallback 解析: 给定一个 ModelConfig, 决定下一个模型
# ============================================================


def resolve_chain(
    config: Config,
    primary: ModelConfig,
    prompt_estimate: int = 0,
) -> list[ModelConfig]:
    """把 model.fallback.chain (字符串列表) 解析成 ModelConfig 列表。

    会自动跳过:
        - chain 里写错了找不到的 model name (warning 但不报错)
        - api_key 没配的 (走 fallback 是为了恢复, 没 key 显然不行)
        - 自身 (a -> a 这种循环写法)
        - mode mismatch (chat vs embedding)
        - **BL-FALLBACK-PROMPT-CAP (5/14)**: tier=public 且 prompt_estimate > config.max_fallback_prompt_tokens
    """
    if not primary.fallback or not primary.fallback.chain:
        return []
    # getattr 兼容老 test (用 SimpleNamespace 没这字段) — 默认 0 = 关功能
    cap = getattr(config, "max_fallback_prompt_tokens", 0)
    out: list[ModelConfig] = []
    for name in primary.fallback.chain:
        if name == primary.name:
            logger.warning(
                "fallback chain self-reference: %s -> %s skipped",
                primary.name, name,
            )
            continue
        m = config.get_model(name)
        if m is None:
            logger.warning(
                "fallback chain refers to unknown model: %s (in chain of %s)",
                name, primary.name,
            )
            continue
        if not m.upstream.is_available:
            logger.info(
                "fallback chain skips %s: api_key (%s) not configured",
                name, m.upstream.api_key_env,
            )
            continue
        if m.mode != primary.mode:
            logger.warning(
                "fallback chain skips %s: mode mismatch (primary=%s, candidate=%s)",
                name, primary.mode, m.mode,
            )
            continue
        # BL-FALLBACK-PROMPT-CAP: 大 prompt 不切公网 (公网更慢更贵, 不该兜底)
        # BL-FALLBACK-CAP-SCOPE (5/15 22:15): 仅 primary 是 private 时才拦 public.
        # 原意是"内网数据不应跨 public 边界", primary 已 public 时 fallback 到 public
        # 不存在新的"跨边界" — 应该允许. 之前漏判 primary.tier 导致 public primary
        # 撞错 fallback chain 全是 public 被一刀切, 抛 LargePromptFallbackBlocked.
        if (
            cap > 0
            and prompt_estimate > cap
            and m.tier == "public"
            and primary.tier == "private"
        ):
            logger.warning(
                "fallback chain skips %s (tier=public, prompt_estimate=%d > cap=%d, "
                "primary=%s/private). BL-FALLBACK-PROMPT-CAP: 大 prompt 内网→公网 "
                "跨边界拦截.",
                name, prompt_estimate, cap, primary.name,
            )
            continue
        out.append(m)
    return out


# ============================================================
# 包装: 异步重试器
# ============================================================


# ── 6/2 BL-TRANSIENT-NETWORK-RETRY (鸿波 6/2 下午 audit) ────────────────────
#
# 现象 (鸿波 log): "每次成功一轮回答, 下一轮就会出错, 再发就正常"
#
# 真 root cause (代码层):
# 1. app.py:1809 `num_retries: 0` — LiteLLM 不重试, 为防业务错被静默吞
# 2. fallback.py BL-FALLBACK-TOGGLE: auto_fallback 默认 False — 业务错直抛
# 3. 私有 LLM 服务器 (10.10.40.102:32730) HTTP keep-alive idle timeout 关连接,
#    aiohttp pool 池里残连下次复用撞 ServerDisconnectedError / Connection reset by peer
#
# 三者叠加 → "残连第一次必撞, 直抛 502, 第二次新建连接好".
#
# 修法: 只对**已知网络层 transient 错**重试 1 次新建连接. 业务错 (4xx/5xx)
# **不动** — 跟 num_retries=0 + auto_fallback=False 原设计语义解耦 (那俩管业务错
# 暴露, 本 retry 管网络残连).

# 异常类名 — 类型层抓
_TRANSIENT_EXC_NAMES = (
    "ServerDisconnectedError",   # aiohttp keep-alive 残连
    "ConnectionResetError",       # OS 层 errno 54 reset
    "ClientOSError",              # aiohttp [Errno 54]
    "ConnectError",               # httpx 包装
    "ReadError",                  # httpx 读时断
    "APIConnectionError",         # openai 包装
)

# 异常消息子串 — litellm 把网络错包成 InternalServerError("OpenAIException - Connection error.")
# 这种 case 类型层抓不到 (变成 litellm.InternalServerError), 必须看消息
_TRANSIENT_MSG_SUBSTRINGS = (
    "Connection error",
    "Server disconnected",
    "reset by peer",
)


def _is_transient_network_error(exc: BaseException) -> bool:
    """检测 HTTP keep-alive idle 残连这类网络层 transient 错.

    跟上游业务错 (400/429/503) 不同 — 业务错重发同样错, 应该走 fallback chain.
    transient 错 client 重新建连接即恢复, retry 1 次省整个 fallback 链消耗.

    匹配两类:
    1. 异常类名 (含 __cause__ / __context__ 链最多 5 层): aiohttp / httpx / openai
       任一层的网络异常 — litellm 套 openai 套 httpx 套 aiohttp 多层嵌套, 最外层
       可能是 InternalServerError, 内层才是 ServerDisconnectedError.
    2. 异常消息含 "Connection error" / "Server disconnected" / "reset by peer" —
       litellm 把网络层错**字符串化**成 InternalServerError 消息, 类型层完全抓不到.
    """
    # 1. 异常链类名扫描
    cur: BaseException | None = exc
    for _ in range(5):
        if cur is None:
            break
        if type(cur).__name__ in _TRANSIENT_EXC_NAMES:
            return True
        cur = cur.__cause__ or cur.__context__
    # 2. 消息层 fallback (litellm 包装的字符串模式)
    msg = str(exc)
    return any(s in msg for s in _TRANSIENT_MSG_SUBSTRINGS)


async def _call_with_transient_retry(invoke_one, model: ModelConfig):
    """对 invoke_one(model) 加 1 次 transient 重试 — 6/2 BL-TRANSIENT-NETWORK-RETRY.

    设计:
    - 只重试**已知网络层 transient** (类名 + 消息双匹配), 业务错原样抛
    - 重试 1 次足够 (残连第二次新建必好), 不指数退避 (transient 错 ~ms 级恢复)
    - 第二次失败说明真上游挂了, 抛给 caller (with_fallback) 决定是否切链
    - aiohttp/httpx connector 看到 ServerDisconnectedError 自动 evict 死连接,
      下次 acquire 新建 — 所以第二次调用必然是 fresh socket
    """
    try:
        return await invoke_one(model)
    except Exception as e:
        if not _is_transient_network_error(e):
            raise
        logger.warning(
            "BL-TRANSIENT-NETWORK-RETRY: model=%s transient %s (%s), 重试 1 次新建连接",
            model.name, type(e).__name__, str(e)[:120],
        )
        # 第 2 次 — 新连接, 残连必好. 仍失败说明真上游挂了, 抛上去让 fallback chain 决定
        return await invoke_one(model)


async def with_fallback(
    config: Config,
    primary: ModelConfig,
    invoke_one,  # async (model: ModelConfig) -> Any
    prompt_estimate: int = 0,  # BL-FALLBACK-PROMPT-CAP (5/14): caller 传的 prompt 估算
) -> tuple[Any, ModelConfig, list[str]]:
    """跑 primary, 失败按 chain 重试。

    invoke_one 是调用方的 closure: 接 ModelConfig 返回 result (or 抛异常)。
    我们这里只管 retry / chain 选择 / hops 计数, 不关心怎么调 LiteLLM。

    BL-FALLBACK-PROMPT-CAP (5/14): 大 prompt (> config.max_fallback_prompt_tokens) 时,
    fallback 链跳过所有 tier=public 的 candidate (公网更慢更贵, 不该兜底).
    调用方用 estimate_prompt_tokens(messages) 算 prompt_estimate 传进来.

    BL-FALLBACK-TOGGLE (2026-05-16 鸿波):
      config.auto_fallback=False (默认) → 任何错都直抛, 不走 chain.
      操作员/客户要恢复老 fallback 行为, 改 yaml `auto_fallback: true` 或
      env `CATFISH_AUTO_FALLBACK=1`. 代码 + chain 配置都保留作 escape hatch.

    返回:
        (result, model_used, attempts) —— 调用方需要知道实际用了哪个 model 写 metrics

    全失败:
        抛最后一次异常 (最新错最有诊断价值)
    """
    attempts: list[str] = []  # 记录尝试过的 model name + 错误概述

    # BL-FALLBACK-TOGGLE: env override 高于 yaml. 默认 False (不 fallback).
    import os  # noqa: PLC0415
    auto_fallback = (
        os.environ.get("CATFISH_AUTO_FALLBACK", "").lower() in ("1", "true", "yes")
        or bool(getattr(config, "auto_fallback", False))
    )

    # 第一次: 主模型
    # 6/2 BL-TRANSIENT-NETWORK-RETRY: invoke_one 包 transient retry — 残连第一次必撞,
    # 第二次新建必好. 业务错 (4xx/5xx) 不动, 仍原样抛走 fallback / 直抛.
    try:
        result = await _call_with_transient_retry(invoke_one, primary)
        attempts.append(f"{primary.name}=ok")
        return result, primary, attempts
    except Exception as e:
        attempts.append(f"{primary.name}=err:{type(e).__name__}")

        # BL-FALLBACK-TOGGLE: 默认关 → 直接抛, 不进 chain.
        if not auto_fallback:
            logger.info(
                "model=%s err=%s; auto_fallback=False (BL-FALLBACK-TOGGLE), "
                "直接抛给客户端 — 不自动切其它 model. 想恢复 fallback: "
                "yaml 加 auto_fallback: true 或 env CATFISH_AUTO_FALLBACK=1",
                primary.name, type(e).__name__,
            )
            raise

        if not primary.fallback:
            raise
        if not should_fallback(e, primary.fallback.on_errors):
            logger.info(
                "model=%s err=%s 不在 on_errors 里, 不 fallback",
                primary.name, type(e).__name__,
            )
            raise
        last_exc: Exception = e

    # fallback chain (BL-FALLBACK-PROMPT-CAP: 传 prompt_estimate 让 resolve_chain 过滤公网)
    candidates = resolve_chain(config, primary, prompt_estimate=prompt_estimate)
    # 边界: 全部 candidate 被 cap 过滤光 → 抛专门错误 (caller 转友好 503)
    cap = getattr(config, "max_fallback_prompt_tokens", 0)
    if not candidates and cap > 0 and prompt_estimate > cap:
        # 区分 "chain 本来就空" vs "chain 被 cap 过滤光"
        raw_chain_len = len(primary.fallback.chain) if primary.fallback else 0
        if raw_chain_len > 0:
            logger.warning(
                "BL-FALLBACK-PROMPT-CAP: 大 prompt (%d > cap=%d) chain 全是公网被过滤光, "
                "primary %s 失败后无 fallback. 抛 LargePromptFallbackBlocked.",
                prompt_estimate, cap, primary.name,
            )
            raise LargePromptFallbackBlocked(
                primary_name=primary.name,
                prompt_estimate=prompt_estimate,
                cap=cap,
                last_exc=last_exc,
                blocked_chain=list(primary.fallback.chain),
            )
    max_hops = primary.fallback.max_hops if primary.fallback else 0
    for i, candidate in enumerate(candidates):
        if i >= max_hops:
            logger.warning(
                "fallback chain hops 已达上限 max_hops=%d, 停止重试",
                max_hops,
            )
            break
        logger.info(
            "fallback hop %d/%d: %s -> %s (因 %s)",
            i + 1, max_hops, primary.name, candidate.name,
            type(last_exc).__name__,
        )
        try:
            # 6/2 BL-TRANSIENT-NETWORK-RETRY: candidate 也包 transient retry
            result = await _call_with_transient_retry(invoke_one, candidate)
            attempts.append(f"{candidate.name}=ok")
            logger.info(
                "fallback succeeded after %d hop(s): %s served (primary was %s)",
                i + 1, candidate.name, primary.name,
            )
            return result, candidate, attempts
        except Exception as e:
            attempts.append(f"{candidate.name}=err:{type(e).__name__}")
            if not should_fallback(e, primary.fallback.on_errors):
                # 这个候选挂了但不是 fallback-able 的错 —— 可能 prompt 本身有问题
                # 直接抛, 因为切下一个模型也会同样挂
                logger.warning(
                    "fallback candidate %s 挂了 (非 fallback-able): %s",
                    candidate.name, type(e).__name__,
                )
                raise
            last_exc = e
            continue

    # chain 跑完都挂
    logger.error(
        "fallback chain 全失败. attempts=%s",
        " -> ".join(attempts),
    )
    raise last_exc
