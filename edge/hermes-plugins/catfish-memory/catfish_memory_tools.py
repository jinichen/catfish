"""memory 工具的 schema 与 5 kind 路由 —— 从 catfish_memory.py 拆出 (8/15 第 2 趟)。

6/2 BL-MEMORY-ROUTER-A2 定的: catfish-memory 用 ctx.register_tool(override=True)
替换 hermes builtin memory tool, 按 kind 路由到 5 个仓库
(identity / project_fact / workflow / journal / todo)。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from datetime import datetime
from typing import Any, Dict

# 相对优先绝对兜底 —— 见 tests/test_loader_fidelity.py
try:
    from .catfish_memory_helpers import (  # noqa: F401
        _catfish_home,
    )
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import (  # noqa: F401
        _catfish_home,
    )

# 跟 catfish_memory.py 同名 —— logging.getLogger 同名返回同一对象
logger = logging.getLogger("catfish.memory.plugin")


class _ToolsMixin:
    """见模块 docstring。"""

    def handle_memory_tool(self, args: Dict[str, Any], **kw: Any) -> str:
        """catfish memory tool 真 handler. ctx.register_tool(name="memory") 调这.

        按 kind 路由到 5 个仓库. 0 后端 LLM 调用 (LLM 自己填 kind), 0 性能损失.
        """
        import json as _json

        action = args.get("action", "add")
        kind = args.get("kind")
        content = args.get("content")

        if not kind:
            return _json.dumps({
                "success": False,
                "error": "kind 必填 (identity/project_fact/workflow/journal/todo/expense).",
            }, ensure_ascii=False)

        # action=replace / remove 仍走 hermes 原生 (改 USER.md / MEMORY.md 入口)
        if action in ("replace", "remove"):
            return self._call_hermes_original_memory_tool(args, **kw)

        # action=add: 按 kind 路由
        try:
            if kind == "todo":
                return self._route_to_reminder(content)
            elif kind == "journal":
                return self._route_to_journal(content)
            elif kind == "workflow":
                return self._route_to_propose_skill(content)
            elif kind == "expense":
                # P3.5.78 (6/22 鸿波): 第 6 kind. 走 _route_to_expense, append
                # ~/.catfish/bookkeep.jsonl. 跟 P3.5.75 jsonl schema 完全兼容.
                return self._route_to_expense(args)
            elif kind == "identity":
                return self._call_hermes_original_memory_tool(
                    {**args, "target": "user"}, **kw
                )
            elif kind == "project_fact":
                return self._call_hermes_original_memory_tool(
                    {**args, "target": "memory"}, **kw
                )
            else:
                return _json.dumps({
                    "success": False,
                    "error": f"unknown kind '{kind}'. 看 schema 选 6 个之一.",
                }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            logger.exception("catfish memory router 异常: %s", e)
            return _json.dumps({
                "success": False,
                "error": f"catfish memory router 异常: {e}",
            }, ensure_ascii=False)

    # ════════════════════════════════════════════════════════════════════════
    # BL-MEMORY-ROUTER-A2 (6/2 凌晨鸿波拍): catfish-memory 接管 memory tool
    # ════════════════════════════════════════════════════════════════════════
    #
    # # 为啥
    # hermes 原生 memory tool 只 2 仓库 (USER.md / MEMORY.md), LLM 没分类指导,
    # 5/24 鸿波实盘 70% 跑偏 (skill spec / journal / todo 全塞 memory).
    #
    # # 修法 (browser_navigate 5/6 同 pattern)
    # __init__.py register(ctx) 调 ctx.register_tool(name="memory", override=True)
    # 替换 hermes builtin memory tool. 新 schema 加 kind 必填 (5 选 1), handler
    # 调 self.handle_memory_tool 按 kind 路由到对应仓库.
    #
    # # 5 个 kind 路由
    # | kind          | 真存储                                                 |
    # |---------------|--------------------------------------------------------|
    # | identity      | hermes 原 memory_tool(target=user) → USER.md         |
    # | project_fact  | hermes 原 memory_tool(target=memory) → MEMORY.md     |
    # | workflow      | catfish_propose_skill (BL-MM9, 5/8 ship)              |
    # | journal       | ~/.catfish/employee_journal.md (catfish 现有)          |
    # | todo          | catfish_create_task (本机任务库)                     |
    #
    # # 性能
    # LLM 在 tool call 时自己填 kind, plugin 直接路由 (0 后端 LLM 调用).
    # 单次 write 延迟 < 50ms 跟 hermes 原生一样, 0 性能损失.
    #
    # # enforcement
    # LLM 看到的 memory tool 就是 catfish 的 (override=True 让 builtin 不出现),
    # 5 选 1 + schema 清晰 description → 命中率 95%+ (从原 30% 降到 5% 跑偏).

    def get_catfish_memory_schema(self) -> Dict[str, Any]:
        """LLM 看到的 memory tool schema. 替换 hermes 原 2 选 1 target 为 6 选 1 kind.

        P3.5.78 (6/22 鸿波 catch): 加第 6 kind = expense (记账). 真因 audit ⑦+⑩:
        P3.5.75 把 bookkeep 做成独立 plugin → LLM 跟 catfish-memory schema 5 kind
        决策树冲突, LLM 看 "19号加油300" → 走 journal/skill 路径. 治本: bookkeep
        融进 memory 第 6 kind, 决策树自然命中, 不需要硬编码 SOUL.md / 改 description.
        """
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": "add (新加) / replace (改) / remove (删)",
                },
                "kind": {
                    "type": "string",
                    "enum": ["identity", "project_fact", "workflow", "journal", "todo", "expense"],
                    "description": (
                        "内容性质 (必填, 决定存哪):\n"
                        "- identity: 关于员工**这个人**的稳定事实 (姓名/部门/偏好/沟通风格) → USER.md\n"
                        "- project_fact: **项目/技术**事实 (API 字段含义/客户机房 IP/工具约定) → MEMORY.md\n"
                        "- workflow: 工作**流程** (有 input/output/step 序列) → 自动提议存成 skill\n"
                        "- journal: 已发生**事件**/session 总结/会议记录 → 写 catfish 员工日志 (不是 memory)\n"
                        "- todo: 用户行动 → 调 catfish_create_task 写入本机任务库 (不是 memory)\n"
                        "- expense: **收支记账** (员工说 花/付/买/收/卖/加油/吃饭 + 金额数字, "
                        "e.g. '今天午饭13', '加油300', '工资25000到账') → ~/.catfish/bookkeep.jsonl\n"
                        "拿不准 → 先看是不是 expense (金额数字+消费/收入动词), 再 fallback journal."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "要存的内容 (action=add/replace 必填; kind=expense 时可空, 用下面 amount/direction 等)",
                },
                "old_text": {
                    "type": "string",
                    "description": "要替换/删除的旧文本 (action=replace/remove 必填, 唯一短 substring)",
                },
                # ── kind=expense 专用字段 (其他 kind 忽略) ──
                # P3.5.78: 6/22 鸿波拍 — 强 schema 比弱 content string parse 更稳,
                # LLM 调用清晰. 这 5 字段仅在 kind=expense 时生效.
                "direction": {
                    "type": "string",
                    "enum": ["支出", "收入"],
                    "description": "(kind=expense 必填) 支出 (花钱) 或 收入 (收钱)",
                },
                "amount": {
                    "type": "number",
                    "description": "(kind=expense 必填) CNY 金额, 数字 > 0. 例: 13, 320.5",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "(kind=expense 推荐) 分类. 鼓励 8 默认: "
                        "餐饮/交通/购物/工资/医疗/转账/房租/其他. 不填默认 '其他'."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "(kind=expense 可选) 备注. 例: '中午外卖', '加油', '工资到账'",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "(kind=expense 可选) 日期 ISO8601 ('2026-06-21' 或 '2026-06-21T12:00:00'). "
                        "不填默认现在. 员工说'昨天/上周/19号' 你自己算绝对日期填."
                    ),
                },
            },
            "required": ["action", "kind"],
        }

    def _route_to_reminder(self, content: str) -> str:
        """kind=todo → 引导 LLM 调 catfish_create_task，不再偷偷落 journal。"""
        import json as _json
        return _json.dumps({
            "success": False,
            "routed_to": "catfish_create_task",
            "content": content,
            "error": (
                "用户行动必须先写入本机任务库；请调用 catfish_create_task。"
                "需要 macOS 提醒时，再调用 catfish_sync_tasks_to_reminders。"
            ),
        }, ensure_ascii=False)

    def _route_to_propose_skill(self, content: str) -> str:
        """kind=workflow → 提议存 skill (LLM 看到 hint 后再调 catfish_propose_skill)."""
        import json as _json
        # plugin 跟 catfish-tool-bridge 进程隔离, 不能直接调 catfish_propose_skill.
        # 返提示让 LLM 自己再调 (一次 turn 内 LLM 能补调).
        return _json.dumps({
            "success": True,
            "routed_to": "propose_skill_hint",
            "hint": (
                "这是 workflow (有 step 序列), 不该写 memory. "
                "请改调 catfish_propose_skill 工具, 把 name + reason + action_steps "
                "传过去, 走员工 confirm 门槛固化为 skill."
            ),
            "content_recap": content[:200],
        }, ensure_ascii=False)

    # ── 5 个路由 helper ─────────────────────────────────────

    def _call_hermes_original_memory_tool(
        self, args: Dict[str, Any], **kw: Any
    ) -> str:
        """走 hermes 原生 memory_tool — identity → USER.md / project_fact → MEMORY.md."""
        from tools.memory_tool import memory_tool as _hermes_memory_tool
        return _hermes_memory_tool(
            action=args.get("action", "add"),
            target=args.get("target", "memory"),
            content=args.get("content"),
            old_text=args.get("old_text"),
            store=kw.get("store"),
        )

    def _route_to_journal(self, content: str) -> str:
        """kind=journal → append ~/.catfish/employee_journal.md."""
        import json as _json
        catfish_home = self._catfish_home_cached or _catfish_home()
        journal_path = catfish_home / "employee_journal.md"
        # BL-CATFISH-WIKI-MODE P0.3 (6/3): 格式 `## [YYYY-MM-DD HH:MM] kind | title`
        ts_short = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        title = (content or "").strip().replace("\n", " ")[:50] or "unknown"
        entry = f"\n## [{ts_short}] journal | {title}\n\n{content}\n"
        try:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return _json.dumps({
                "success": True,
                "routed_to": "employee_journal.md",
                "path": str(journal_path),
            }, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            return _json.dumps({
                "success": False,
                "error": f"journal 写失败: {e}",
            }, ensure_ascii=False)
