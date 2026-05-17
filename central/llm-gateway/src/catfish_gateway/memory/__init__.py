"""BL-MEMORY-PROVIDER-ABC (5/16 鸿波 'inject 改 FTS5 + memory_distill 后, 该抽接口').

# 为啥 (鸿波早上 audit 发现)

catfish 9 个记忆模块各干各的:
  inject_session_history / inject_employee_journal / inject_session_facts /
  inject_session_meta / inject_feedback / inject_skills_catalog /
  memory_distill / conversation_compressor / tool_archive

各自的问题:
  - 注入顺序写死在 app.py middleware (改顺序要动主路径)
  - 各 inject 没预算共识, 加起来可能超 system prompt 设计上限
  - 加新维度 (例 vector RAG) 必须再开一个 module + 改 app.py chain
  - 没办法整体看"这次 chat 注入了什么内容多大", 排查只能看 9 处分散 log
  - hermes 0.13 已经有干净 MemoryProvider ABC (prefetch/sync_turn/on_pre_compress),
    我们重造轮子半年, 没收敛

# 设计

借鉴 hermes 0.13 但适配 catfish 企业级关注 (multi-tenant / RBAC / audit):

  class MemoryProvider:
      name: str                        # 'session_history' / 'employee_journal' / ...
      priority: int                    # 决定 inject 顺序 (低数先 inject)
      budget_bytes: int                # 这 provider 在 system prompt 里的预算上限

      def prefetch(self, ctx) -> str | None
          # turn 前召回: 拿 ctx (user / current_messages / model_name) → 生成 inject 内容
          # 返 None 表示这次不 inject
          # 内容自动按 budget_bytes 截断 (Registry 做)

      def sync_turn(self, ctx, assistant_response)  # noqa: 留 hook 给后续阶段
          # turn 后持久化: 借鉴 hermes memory tool 主动写. 现在阶段 stub.

      def on_pre_compress(self, messages) -> messages  # noqa: 留 hook 给后续阶段
          # compressor 压缩前 hook: 让 provider 改写 messages (例: 保留它管理的关键
          # message). 现在阶段 stub.

# 跟 hermes 不同的扩展

1. **budget_bytes** — hermes 不管这个 (它注入 SOUL.md 全文). catfish 必须管,
   因为 9 个 provider 加起来撞 system prompt 上限. Registry 按 priority 顺序分配.

2. **inject_priority** — hermes 各 provider 独立运行 (没显式排序). catfish 借
   priority 字段统一管 inject 顺序, 替代 app.py 写死 chain.

3. **企业治理 hook** — provider 包装层加 audit / RBAC. internal call (loopback)
   可以跳整个 Registry, 跟 BL-MEMORY-POLISH 修法对齐.

# 当前阶段 (5/16 鸿波下令做 PoC)

1. **接口 + Registry**: 跑通框架 (本文件 + registry.py)
2. **1 个 PoC provider**: 包 inject_feedback (最小独立, 风险低)
3. **不动现有 8 个 inject 调用**: app.py middleware chain 暂时不变, 等接口验证 OK
   再批量迁

# 后续 P1 迁移规划

按风险从低到高:
  Step 1: feedback_inject → MemoryProvider (本次 PoC) ✓
  Step 2: session_meta / session_facts (简单, 各 ~50 行)
  Step 3: employee_journal / session_history (中, 各 ~200 行)
  Step 4: memory_distill (跟蒸馏后台 task 配合)
  Step 5: tool_archive / conversation_compressor (复杂, 跟 streaming + DB 强耦合)
  Step 6: 等 hermes USER.md 这条管道稳定后, 再决定砍 catfish 自己的 employee_journal

每 Step 单独 sprint, 不一口气推.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class InjectContext:
    """传给 MemoryProvider.prefetch() 的上下文.

    所有 provider 共享同一份 ctx, 防各 provider 重复算同样的派生数据 (例
    last_user_message 抽取). Registry 在 inject() 入口算一次填好.
    """

    #: 当前 user (sub / dept / role). None = 匿名 (catalog 探查阶段)
    user_sub: str | None = None
    user_dept: str | None = None
    user_role: str | None = None

    #: 这次 chat 的 messages (provider 可只读, 不该改 — 改用 prefetch 返字符串)
    messages: list[dict[str, Any]] = field(default_factory=list)

    #: 当前 user message 文本 (Registry 抽好, 各 provider 用同一份)
    last_user_message: str = ""

    #: 模型名 (例 'catfish-private-main') — provider 可按模型差异化 (例 vision model
    #: 跳过文本类 memory inject)
    model_name: str | None = None

    #: 是否 internal call (loopback summarizer/distill 等). True → Registry 跳整 inject
    is_internal_call: bool = False

    #: 调试模式 — Registry 打印每 provider 实际 inject 字节数 / 截断 (audit 用)
    debug: bool = False

    #: BL-RBAC-DAY5 (5/17): user.effective_allowed_skills glob list (从 OIDC claim).
    #: [] = 全允许 (开放默认). SkillsCatalogProvider 用 fnmatch 过滤 skill 列表.
    #: None = 没设 (匿名 / 内部调用), 不过滤.
    effective_allowed_skills: list[str] | None = None

    #: BL-RBAC (5/10 BL-ARCH1 P1): user 是不是 sysadmin (绕过所有 RBAC).
    is_sysadmin: bool = False


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆 provider 协议. 实现者不需要继承, duck-typing.

    最小实现 (只用 prefetch):

        class MyProvider:
            name = "my_memory"
            priority = 50
            budget_bytes = 2000

            def prefetch(self, ctx: InjectContext) -> str | None:
                if not ctx.user_sub:
                    return None  # 匿名跳过
                return f"## 我的记忆\n\n...{ctx.user_sub}..."

            # 其它 hook 都可选 (没实现 = noop)
    """

    #: provider 唯一名 (registry key, 不能跟其它 provider 撞)
    name: str

    #: inject 顺序优先级. 数小先 inject, 大后 inject. 推荐档:
    #:   10  identity / persona (hermes SOUL.md 类)
    #:   20  session_facts (员工显式硬事实)
    #:   30  session_meta (时间元)
    #:   40  skills_catalog
    #:   50  session_history (跨 session 元信息)
    #:   60  employee_journal (跨 session 内容总结)
    #:   70  feedback (员工反馈 — 末尾警示位置最强)
    priority: int

    #: 这 provider 在 system prompt 里能用的字节预算上限. Registry 截断保护.
    #:   2000 ≈ 500 token 适合 feedback / facts / meta 这种短结构化内容
    #:   8000 ≈ 2K token 适合 journal / distilled_facts 这种长内容
    #:   20000 ≈ 5K token 适合 skills_catalog (有 tool schema 详细描述)
    budget_bytes: int

    def prefetch(self, ctx: InjectContext) -> str | None:
        """turn 前召回: 算这 provider 要 inject 的内容. 返 None 表示这次不 inject.

        返字符串会被 Registry 自动截断到 budget_bytes (从尾部截, 保留前段).
        provider 可以自己更精细截 (例按段边界截) 后返已合规字符串.

        失败 (DB 错 / IO 错) 不抛, 返 None — Registry 跳过这 provider, 不影响其它.
        """
        ...
