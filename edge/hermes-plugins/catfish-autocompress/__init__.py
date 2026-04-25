"""鲶鱼自动压缩 —— 轻量 wrapper，包装 Hermes 内置 ContextCompressor。

为什么要 wrap 不直接继承：
    ContextCompressor.__init__ 需要 model / base_url / api_key 等参数，
    这些参数在插件 register() 时还不知道（Hermes 还没决定用哪个模型）。
    所以我们在 register 时只创建一个"薄壳"，等 on_session_start 时 Hermes
    会告诉我们 model 信息，那时再延迟创建真正的 ContextCompressor。

为什么做这个插件：
    Hermes 内置 ContextCompressor 默认 threshold_percent=0.5（约 64K 触发，
    对 128K 的 Qwen 来说频繁压缩会打断业务流，每次压缩也有 summary 调用开销）。
    鲶鱼员工日常跑浏览器自动化 + 合规平台连续查询，希望"少压一点但压得准"。
    把阈值提到 0.70（约 90K 触发），一个 session 通常只压 1~2 次。

配置：
    CATFISH_COMPRESS_THRESHOLD=0.75  环境变量（0.10~0.95，默认 0.70）

启用：
    ~/.hermes/config.yaml 加：
        context:
          engine: catfish-autocompress
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from agent.context_compressor import ContextCompressor
from agent.context_engine import ContextEngine

logger = logging.getLogger("catfish.autocompress")

DEFAULT_THRESHOLD = 0.70
MIN_THRESHOLD = 0.10
MAX_THRESHOLD = 0.95


def _resolve_threshold() -> float:
    raw = os.environ.get("CATFISH_COMPRESS_THRESHOLD")
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        v = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "CATFISH_COMPRESS_THRESHOLD=%r 不是合法浮点数，回默认 %.2f",
            raw, DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    if not (MIN_THRESHOLD <= v <= MAX_THRESHOLD):
        logger.warning(
            "CATFISH_COMPRESS_THRESHOLD=%.3f 超出 [%.2f, %.2f]，回默认 %.2f",
            v, MIN_THRESHOLD, MAX_THRESHOLD, DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    return v


class CatfishAutoCompress(ContextEngine):
    """包装 ContextCompressor，把触发阈值改到 catfish 期望值。

    实例化流程：
        1. Hermes 加载插件 → `register()` → `CatfishAutoCompress()` 空壳
        2. Hermes 开 session → `on_session_start(session_id, model=..., api_base=...)`
           → 这时才真正创建 ContextCompressor(model=..., threshold=catfish值)
        3. 之后 update_from_response / should_compress / compress 都代理给内部 CC

    如果 Hermes 没传 model（防御性编程），所有方法都退化成"不压缩"，
    这样至少不崩。
    """

    @property
    def name(self) -> str:
        return "catfish-autocompress"

    def __init__(self) -> None:
        # 不调 super().__init__() —— ContextEngine ABC 没有 __init__，
        # 但如果 ABC 未来加了 __init__ 需要参数就会在这里挂。保持明确。
        self._inner: ContextCompressor | None = None
        self._configured_threshold = _resolve_threshold()

        # 覆盖 ABC 的 class variable 0.75。这样 Hermes 外部（状态栏、/config、
        # discovery 逻辑）读到的是 catfish 配置后的真实阈值，跟内部 inner 的行为对齐。
        self.threshold_percent = self._configured_threshold

        logger.info(
            "catfish-autocompress 已注册：threshold=%.0f%%（实际 ContextCompressor "
            "会在 on_session_start 时创建）",
            self._configured_threshold * 100,
        )

    # -- 生命周期：真正实例化 inner ContextCompressor 的点 ---------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        if self._inner is None:
            self._inner = self._build_inner(**kwargs)
        if self._inner is not None:
            # 把基类公开的 state 字段同步上来，run_agent.py 读它们打状态栏
            self._inner.on_session_start(session_id, **kwargs)

    def _build_inner(self, **kwargs) -> ContextCompressor | None:
        """基于 Hermes 传来的 session 上下文创建真正的 ContextCompressor。

        Hermes 可能在 on_session_start 传 model / api_base / api_key / provider / api_mode 等。
        我们按 ContextCompressor.__init__ 的签名挑需要的传过去，其他用默认。
        """
        model = kwargs.get("model")
        if not model:
            logger.warning(
                "on_session_start 没拿到 model 参数，无法创建 ContextCompressor。"
                "自动压缩退化成 no-op（本轮不压缩）。kwargs=%s",
                list(kwargs),
            )
            return None

        # 参数映射：只挑 ContextCompressor.__init__ 签名里有的字段
        # 用 get() 取值，没有就走默认。
        init_kwargs: dict[str, Any] = {
            "model": model,
            "threshold_percent": self._configured_threshold,
        }
        for key in (
            "base_url", "api_key", "provider", "api_mode",
            "protect_first_n", "protect_last_n",
            "summary_target_ratio", "summary_model_override",
            "config_context_length", "quiet_mode",
        ):
            if key in kwargs:
                init_kwargs[key] = kwargs[key]

        try:
            inner = ContextCompressor(**init_kwargs)
        except TypeError as e:
            logger.warning(
                "ContextCompressor(**%s) 签名不匹配：%s —— 退化到 model-only 构造",
                init_kwargs, e,
            )
            # 保底：只传 model + threshold
            try:
                inner = ContextCompressor(
                    model=model,
                    threshold_percent=self._configured_threshold,
                )
            except Exception as e2:
                logger.error("ContextCompressor 最小构造也失败：%s", e2)
                return None

        logger.info(
            "catfish-autocompress 接管：model=%s threshold=%.0f%% inner=%s",
            model, self._configured_threshold * 100, type(inner).__name__,
        )
        return inner

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        if self._inner is not None:
            self._inner.on_session_end(session_id, messages)

    def on_session_reset(self) -> None:
        if self._inner is not None:
            self._inner.on_session_reset()
        else:
            super().on_session_reset()

    # -- 核心接口代理 -----------------------------------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        if self._inner is not None:
            self._inner.update_from_response(usage)
            # 把 inner 的 state 回填到自己身上，方便 Hermes 读
            self.last_prompt_tokens = self._inner.last_prompt_tokens
            self.last_completion_tokens = self._inner.last_completion_tokens
            self.last_total_tokens = self._inner.last_total_tokens
            self.context_length = self._inner.context_length
            self.threshold_tokens = self._inner.threshold_tokens

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        if self._inner is None:
            return False
        triggered = self._inner.should_compress(prompt_tokens)
        if triggered:
            pt = prompt_tokens if prompt_tokens is not None else self._inner.last_prompt_tokens
            cl = getattr(self._inner, "context_length", 0) or 0
            pct = (pt / cl * 100) if cl else 0
            logger.info(
                "触发自动压缩：prompt_tokens=%d context_length=%d (%.0f%% >= %.0f%% 阈值) "
                "compression_count=%d",
                pt, cl, pct, self._configured_threshold * 100,
                getattr(self._inner, "compression_count", 0),
            )
        return triggered

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        if self._inner is None:
            return False
        return self._inner.should_compress_preflight(messages)

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int | None = None,
    ) -> List[Dict[str, Any]]:
        if self._inner is None:
            # 没 inner 就不压，原样返回
            return messages
        result = self._inner.compress(messages, current_tokens=current_tokens)
        # 同步 compression_count
        self.compression_count = self._inner.compression_count
        return result

    # -- 可选接口（tool schemas 等）也代理 --------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if self._inner is None:
            return []
        return self._inner.get_tool_schemas()

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        if self._inner is None:
            import json
            return json.dumps({"error": "catfish-autocompress inner not ready"})
        return self._inner.handle_tool_call(name, args, **kwargs)


def register(ctx) -> None:
    ctx.register_context_engine(CatfishAutoCompress())
