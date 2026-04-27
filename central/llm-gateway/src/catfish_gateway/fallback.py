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
from typing import Any, Iterable

from .config import Config, FallbackConfig, ModelConfig

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
) -> list[ModelConfig]:
    """把 model.fallback.chain (字符串列表) 解析成 ModelConfig 列表。

    会自动跳过:
        - chain 里写错了找不到的 model name (warning 但不报错)
        - api_key 没配的 (走 fallback 是为了恢复, 没 key 显然不行)
        - 自身 (a -> a 这种循环写法)
    """
    if not primary.fallback or not primary.fallback.chain:
        return []
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
        out.append(m)
    return out


# ============================================================
# 包装: 异步重试器
# ============================================================


async def with_fallback(
    config: Config,
    primary: ModelConfig,
    invoke_one,  # async (model: ModelConfig) -> Any
) -> tuple[Any, ModelConfig, list[str]]:
    """跑 primary, 失败按 chain 重试。

    invoke_one 是调用方的 closure: 接 ModelConfig 返回 result (or 抛异常)。
    我们这里只管 retry / chain 选择 / hops 计数, 不关心怎么调 LiteLLM。

    返回:
        (result, model_used, attempts) —— 调用方需要知道实际用了哪个 model 写 metrics

    全失败:
        抛最后一次异常 (最新错最有诊断价值)
    """
    attempts: list[str] = []  # 记录尝试过的 model name + 错误概述

    # 第一次: 主模型
    try:
        result = await invoke_one(primary)
        attempts.append(f"{primary.name}=ok")
        return result, primary, attempts
    except Exception as e:
        attempts.append(f"{primary.name}=err:{type(e).__name__}")
        if not primary.fallback:
            raise
        if not should_fallback(e, primary.fallback.on_errors):
            logger.info(
                "model=%s err=%s 不在 on_errors 里, 不 fallback",
                primary.name, type(e).__name__,
            )
            raise
        last_exc: Exception = e

    # fallback chain
    candidates = resolve_chain(config, primary)
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
            result = await invoke_one(candidate)
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
