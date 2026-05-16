"""MemoryProvider 注册中心 + inject 协调器.

# 职责

1. 注册 provider (register / get / list)
2. inject_all(ctx, messages) — 按 priority 顺序跑所有 provider.prefetch(),
   按 budget_bytes 截断, 注入到 system message 末尾
3. internal_call 跳整个 inject (BL-MEMORY-POLISH 修法的统一替代)
4. debug 模式打字节统计

# 使用

    registry = MemoryRegistry()
    registry.register(FeedbackProvider())
    registry.register(SessionFactsProvider())
    ...
    out_messages = registry.inject_all(ctx, messages)

# 不做的

- **不取代现有 9 个 inject 函数** (PoC 阶段). 现有 inject 函数继续在 app.py 调用,
  Registry 当**新加 provider 的入口** 跑. 等迁完才把现有 inject_X() 包装成 provider.
- **不做 sync_turn / on_pre_compress hook**. 留接口字段, 实际是 stub.

# 全局 singleton

Registry 是 process-wide singleton (gateway 进程内一份). 启动时 register 完, 之后
所有 chat completions 复用. 避免重复初始化 provider 实例.
"""

from __future__ import annotations

import logging

from . import InjectContext, MemoryProvider

logger = logging.getLogger("catfish.gateway.memory.registry")


class MemoryRegistry:
    """provider 注册 + inject 协调."""

    def __init__(self) -> None:
        self._providers: dict[str, MemoryProvider] = {}

    def register(self, provider: MemoryProvider) -> None:
        """注册一个 provider. 同名重复注册 → 替换 + warning.

        provider 必须满足 MemoryProvider 协议 (name / priority / budget_bytes /
        prefetch). runtime_checkable Protocol 自动检查.
        """
        if not isinstance(provider, MemoryProvider):
            raise TypeError(
                f"register({provider!r}): 不满足 MemoryProvider 协议 "
                "(需 name / priority / budget_bytes / prefetch)"
            )
        if provider.name in self._providers:
            logger.warning(
                "memory_registry: provider %r 重复注册, 替换. 旧实例丢失",
                provider.name,
            )
        self._providers[provider.name] = provider
        logger.info(
            "memory_registry: 注册 %s (priority=%d, budget=%d 字节)",
            provider.name, provider.priority, provider.budget_bytes,
        )

    def list_providers(self) -> list[MemoryProvider]:
        """按 priority 升序返所有 provider (debug / audit 用)."""
        return sorted(self._providers.values(), key=lambda p: p.priority)

    def inject_subset(
        self,
        ctx: InjectContext,
        messages: list[dict],
        enabled_names: set[str] | None = None,
    ) -> list[dict]:
        """同 inject_all 但只跑 enabled_names 里的 provider.

        enabled_names=None → 跑全部 (等价于 inject_all).
        enabled_names=set() → 跳全部 (等价于 internal_call).

        用途: app.py 按 lean / internal 模式选 provider 子集. 例如:
          - lean 模式: enabled={'skills_catalog'} (其它全 skip)
          - internal: enabled=set() (全 skip)
          - 普通: enabled=None (全跑)

        语义上 enabled 是**白名单**: 只有名字在集合里的 provider 才会跑.
        unknown name 在 enabled 里安静忽略 (没该 provider).
        """
        if ctx.is_internal_call:
            return messages
        if enabled_names is not None and not enabled_names:
            return messages
        # 按 priority 升序过滤
        all_providers = self.list_providers()
        if enabled_names is None:
            chosen = all_providers
        else:
            chosen = [p for p in all_providers if p.name in enabled_names]
        return self._inject_with_providers(ctx, messages, chosen)

    def inject_all(
        self,
        ctx: InjectContext,
        messages: list[dict],
    ) -> list[dict]:
        """按 priority 顺序跑所有 provider.prefetch(), 把内容拼到最后 system message 末尾.

        流程:
          1. internal_call → 跳整个 (返原 messages 不动)
          2. 按 priority 升序遍历 provider, 调 prefetch(ctx)
          3. 每 provider 返字符串 → 按 budget_bytes 截 (尾部截, 留前段)
          4. 累加所有 provider 内容 → 拼到最后 system message 末尾
          5. debug 模式 → log 每 provider 字节数

        没 system message → 不 inject (返原 messages). provider 失败 (抛异常) → log
        warning, 跳过该 provider, 不影响其它.

        幂等性: provider 自己保证 (Registry 不检查, 因为不同 provider 的 inject
        内容 marker 不同, 集中检查复杂). 推荐 provider 在 prefetch 内做幂等检测
        (例 inject_employee_journal 检 "员工长期日记" 标志).
        """
        if ctx.is_internal_call:
            logger.debug(
                "memory_registry: internal call (loopback), 跳全部 %d provider",
                len(self._providers),
            )
            return messages
        return self._inject_with_providers(ctx, messages, self.list_providers())

    def _inject_with_providers(
        self,
        ctx: InjectContext,
        messages: list[dict],
        providers: list[MemoryProvider],
    ) -> list[dict]:
        """实际跑指定 provider 集合的 inject, 返新 messages.

        共享 inject_all / inject_subset 的核心逻辑. 调用方负责筛选 provider
        + 决定 is_internal_call 跳不跳.
        """
        if not messages or not providers:
            return messages

        # 找最后 system message
        last_system_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "system":
                last_system_idx = i
                break
        if last_system_idx < 0:
            logger.debug("memory_registry: 无 system message, 跳全部 inject")
            return messages

        # 按 priority 跑 provider (已经 sorted, _inject_with_providers 调用方负责)
        injected_parts: list[tuple[str, str, int]] = []  # (name, content, bytes)
        for provider in providers:
            try:
                content = provider.prefetch(ctx)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "memory_registry: provider %r prefetch 失败 (跳过): %s",
                    provider.name, e,
                )
                continue
            if not content:
                continue
            # 按 budget 截 (字节级, 尾部截, 保留前段)
            content_bytes = content.encode("utf-8")
            if len(content_bytes) > provider.budget_bytes:
                truncated = content_bytes[: provider.budget_bytes]
                try:
                    content = truncated.decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    content = content[: provider.budget_bytes // 3]
                logger.info(
                    "memory_registry: %s 内容 %d 字节超 budget %d, 截到 %d",
                    provider.name, len(content_bytes), provider.budget_bytes,
                    len(content.encode("utf-8")),
                )
            injected_parts.append((provider.name, content, len(content.encode("utf-8"))))

        if not injected_parts:
            return messages

        # 拼接所有 inject 内容 → 加到最后 system message 末尾
        from copy import deepcopy  # noqa: PLC0415

        out = deepcopy(messages)
        cur = out[last_system_idx].get("content", "")
        if not isinstance(cur, str):
            return messages
        appended = "\n\n" + "\n\n".join(c for _, c, _ in injected_parts)
        out[last_system_idx]["content"] = cur.rstrip() + appended

        # BL-MEMORY-MIGRATE-STEP1C: 总字节 info 永远打, 不再 debug-only.
        # 生产监控需要这条数据看 budget 设置是否合理 / 总 inject 在什么级别.
        # 之前 5/16 鸿波本机看不到 inject 总计, 改 budget 没数据基础.
        total_bytes = sum(b for _, _, b in injected_parts)
        logger.info(
            "memory_registry: inject %d provider, 总 %d 字节: %s",
            len(injected_parts), total_bytes,
            ", ".join(f"{name}={b}" for name, _, b in injected_parts),
        )

        return out


#: process-wide singleton. gateway 启动时填.
_GLOBAL_REGISTRY: MemoryRegistry = MemoryRegistry()


def get_global_registry() -> MemoryRegistry:
    """获取全局 Registry singleton.

    用 function 不直接 export 实例, 防测试 import 后 monkeypatch 困难
    (测试可以 monkeypatch get_global_registry 整体替换).
    """
    return _GLOBAL_REGISTRY
