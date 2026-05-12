"""BL-HERMES013-5 (5/12 鸿波拍板) — transform_llm_output ABC plugin hook.

借鉴 Hermes 0.13 `transform_llm_output` plugin hook 设计. 把当前散落在
chat_completions finally 块里的 audit / quota / context check 拉到一个统一
拦截链, 后续加新 hook (in-flight 持久化 / Q3 客户定制脱敏 / etc) 只改一处.

# 当前散点 (BL-HERMES013-5 重构前)

```
_stream_chat_completion finally 块 (app.py:1549-1579):
  ├─ _check_context_usage()        — 上下文用量警戒
  ├─ log_request_metadata()         — audit 写盘 (PG + jsonl)
  └─ _quota_module.record_usage()   — quota 计数 (跳 internal)
```

3 个 inline 调用, 添新 hook (例 in-flight 流持久化) 必须改 finally — 容易撞.

# 重构后

```
_stream_chat_completion finally 块 → output_transforms.DEFAULT_CHAIN.run(ctx)
       ↓
    ┌──────────────────────────────────────────┐
    │ TransformChain (顺序执行, 失败静默不传染) │
    ├──────────────────────────────────────────┤
    │ ContextUsageTransform → _check_context_usage  │
    │ AuditTransform        → log_request_metadata  │
    │ QuotaTransform        → record_usage          │
    │ (BL-HERMES013-4 新加: InflightCleanupTransform)│
    └──────────────────────────────────────────┘
```

# 不变量

- 每个 transform 的副作用**独立** (一个 fail 不影响其他, 跟原来 inline 同语义)
- ctx 是只读 dataclass (transform 不准改, 防顺序依赖)
- chain 顺序由 build_default_chain() 决定, 测试可换序 (BL-FED 演化用)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger("catfish.gateway.output_transforms")


# ─────────────────────────────────────────────────────────────
# Context — 只读 dataclass, 所有 transform 共享
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OutputCtx:
    """LLM 流式响应结束时的全部 metadata.

    Frozen — transform 不准改, 防顺序依赖.
    """
    user: str
    department: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    ttft_ms: Optional[float]
    status: str            # "ok" | "error"
    error: str
    security_concern: str
    is_internal: bool
    # used_model 是 catalog ChosenModel — 转 dict/Any 避免循环 import
    used_model: Any = None
    # BL-HERMES013-4 持久化 hook 用: gateway-issued request id
    request_id: str = ""


# ─────────────────────────────────────────────────────────────
# Transform protocol
# ─────────────────────────────────────────────────────────────

class LLMOutputTransform(Protocol):
    """所有 hook 实现这个 protocol. 失败时 chain 会静默 + log warning."""
    name: str

    def on_complete(self, ctx: OutputCtx) -> None: ...
    def on_error(self, ctx: OutputCtx) -> None: ...


# ─────────────────────────────────────────────────────────────
# 内置 transform 实现 (3 个 — 包原 inline 调用)
# ─────────────────────────────────────────────────────────────


class ContextUsageTransform:
    """包 _check_context_usage(used_model, prompt_tokens, user). 只在 status=ok + 有 model 时跑."""
    name = "context_usage"

    def on_complete(self, ctx: OutputCtx) -> None:
        if ctx.used_model is None or ctx.prompt_tokens <= 0:
            return
        # lazy import 防循环 import
        from .app import _check_context_usage  # noqa: PLC0415
        _check_context_usage(ctx.used_model, ctx.prompt_tokens, ctx.user)

    def on_error(self, ctx: OutputCtx) -> None:
        # error 路径 prompt_tokens 可能为 0, 但 context overflow 错误 (BL-FIX23-L4)
        # 也走这个 hook. 跟 on_complete 同, 不区分.
        if ctx.used_model is None or ctx.prompt_tokens <= 0:
            return
        from .app import _check_context_usage  # noqa: PLC0415
        _check_context_usage(ctx.used_model, ctx.prompt_tokens, ctx.user)


class AuditTransform:
    """包 metrics.log_request_metadata. 双路径 (PG + jsonl 兜底), 自带脱敏 (BL-HERMES013-1)."""
    name = "audit"

    def on_complete(self, ctx: OutputCtx) -> None:
        self._write(ctx)

    def on_error(self, ctx: OutputCtx) -> None:
        self._write(ctx)

    @staticmethod
    def _write(ctx: OutputCtx) -> None:
        from .metrics import log_request_metadata  # noqa: PLC0415
        log_request_metadata(
            user=ctx.user,
            model=ctx.model,
            prompt_tokens=ctx.prompt_tokens,
            completion_tokens=ctx.completion_tokens,
            latency_ms=ctx.latency_ms,
            ttft_ms=ctx.ttft_ms,
            status=ctx.status,
            error=ctx.error,
            security_concern=ctx.security_concern,
        )


class QuotaTransform:
    """包 quota.record_usage. **只**在 status=ok + 非 internal + 有 token 时计."""
    name = "quota"

    def on_complete(self, ctx: OutputCtx) -> None:
        if ctx.is_internal:
            # BL-F17 (5/5): internal 调用跳 quota (audit 仍写, 透明)
            return
        if ctx.prompt_tokens <= 0 and ctx.completion_tokens <= 0:
            return
        from . import quota as _quota_module  # noqa: PLC0415
        _quota_module.record_usage(
            user_email=ctx.user,
            department=ctx.department,
            model=ctx.model,
            tokens_in=ctx.prompt_tokens,
            tokens_out=ctx.completion_tokens,
        )

    def on_error(self, ctx: OutputCtx) -> None:
        # error 不计 quota (跟 5/2 sprint 收尾原行为一致)
        return


class InflightCleanupTransform:
    """BL-HERMES013-4 (5/12): stream 完成时 unlink inflight 文件.

    跟 mark_started 配对. 流式开始 mark, 流完 (不管 ok / error) chain 跑这里 unlink.
    崩了 finally 不跑 → 文件留, gateway 启动时 reap_interrupted 写 audit + 清.
    """
    name = "inflight_cleanup"

    def on_complete(self, ctx: OutputCtx) -> None:
        self._unlink(ctx)

    def on_error(self, ctx: OutputCtx) -> None:
        # error 也清 — Python 跑到这里说明 generator 至少正常完成了 try/except,
        # 只是上游 LLM 报错. 真断电 finally 不跑文件留下, 那才是"interrupted"
        self._unlink(ctx)

    @staticmethod
    def _unlink(ctx: OutputCtx) -> None:
        if not ctx.request_id:
            return
        from . import inflight_streams  # noqa: PLC0415
        inflight_streams.mark_finished(ctx.request_id)


# ─────────────────────────────────────────────────────────────
# Chain
# ─────────────────────────────────────────────────────────────


@dataclass
class TransformChain:
    """顺序执行所有 transforms. 一个失败 log warning + 跳到下一个 (静默不传染主流程)."""
    transforms: list[LLMOutputTransform] = field(default_factory=list)

    def run(self, ctx: OutputCtx) -> None:
        for t in self.transforms:
            try:
                if ctx.status == "ok":
                    t.on_complete(ctx)
                else:
                    t.on_error(ctx)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "BL-HERMES013-5 transform %r 失败 (静默, 不阻塞主流程): %s",
                    getattr(t, "name", type(t).__name__), e,
                )

    def add(self, t: LLMOutputTransform) -> "TransformChain":
        """运行时插 transform (测试 / 客户定制用). 返 self 支持链式."""
        self.transforms.append(t)
        return self


# ─────────────────────────────────────────────────────────────
# 默认 chain (gateway 启动时 build 一次, app.py finally 用)
# ─────────────────────────────────────────────────────────────

def build_default_chain() -> TransformChain:
    """默认 4 件: ContextUsage → Audit → Quota → InflightCleanup.

    顺序:
      - ContextUsage 在 Audit 前 (那条 BL-FIX23-L4 警告也想进 audit 字段)
      - Quota 在 Audit 后 (audit 已经把这次 token 落盘, 哪怕 quota 写挂了也
        有审计依据)
      - InflightCleanup 最后 (audit + quota 都跑完才 unlink — 崩在 audit 之
        前文件留下, 重启 reap 写 'interrupted' audit 替补)
    """
    return TransformChain([
        ContextUsageTransform(),
        AuditTransform(),
        QuotaTransform(),
        InflightCleanupTransform(),
    ])


#: 模块级单例. app.py 直接 import 用. 测试可 monkeypatch.
DEFAULT_CHAIN = build_default_chain()


__all__ = [
    "OutputCtx",
    "LLMOutputTransform",
    "ContextUsageTransform",
    "AuditTransform",
    "QuotaTransform",
    "InflightCleanupTransform",
    "TransformChain",
    "build_default_chain",
    "DEFAULT_CHAIN",
]
