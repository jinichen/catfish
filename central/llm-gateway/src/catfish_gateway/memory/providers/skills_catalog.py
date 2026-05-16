"""SkillsCatalogProvider — 包 inject_skills_catalog 成 MemoryProvider.

priority=40: skill 列表是工具元 (在 facts 后, history 前). 模型先看到员工硬事实
+ 时间元, 再看到"有这些 skill 可调".

BL-MEMORY-INJECT-OPTIMIZE (5/16 鸿波 'inject 28KB 超 KPI 2.8x'):
  现状灌 14KB skill catalog 是最大头. 改条件 inject — 只在 user message 含**动作
  意图** (做/写/生成/帮我/运行/分析/...) 时注. 普通 chat (你好/你叫什么) 跳过 14KB.

  风险: 模型不知道有 skill 可调 → 漏调机会. 用宽松匹配 (任何"动词+目标" pattern)
  把漏调概率压到 < 10%. 真有 skill 名字误触发不调, 客户端 catfish_run_skill 兜底.

  Toggle env CATFISH_SKILLS_ALWAYS_INJECT=1 → 走老行为永远 inject (操作员一键回滚).
"""

from __future__ import annotations

import logging
import os
import re

from .. import InjectContext

logger = logging.getLogger("catfish.gateway.memory.providers.skills_catalog")


#: 动作意图启发式正则 — 中英文动词 + 目标 pattern.
#: 命中任意 → 用户大概率想"做事", 注入 skill catalog.
#: 漏了纯查询场景 ("你好" / "你叫什么" / "1+1=?") — 这些不需要 catalog.
_ACTION_INTENT_RE = re.compile(
    # 中文动作动词
    r"做|写|生成|帮我|帮忙|运行|执行|调用"
    r"|分析|创建|制作|建立|起草|起一份"
    r"|打卡|登录|查询|搜索|查看|检查"
    r"|上传|下载|发送|发个|发起"
    r"|汇报|总结|整理"
    # 英文常见
    r"|\bgenerate\b|\bwrite\b|\brun\b|\bexecute\b|\bcreate\b|\bmake\b"
    r"|\bdraft\b|\bplease\b|\banalyze\b|\bsearch\b|\bcheck\b",
    re.IGNORECASE,
)


def _has_action_intent(text: str) -> bool:
    """启发式: text 是不是含'员工想做事' 信号."""
    if not text:
        return False
    return bool(_ACTION_INTENT_RE.search(text))


class SkillsCatalogProvider:
    """注入 catfish skills 列表 (员工可调的自动化技能元信息).

    内容来自 skills_loader.discover_skills() — 扫描 ~/.catfish/skills/ + 公司
    central skills. 实测 ~14 KB (含每 skill triggers + summary).

    条件 inject: 只在 user message 含**动作意图** 时注. CATFISH_SKILLS_ALWAYS_INJECT=1
    一键回滚永远注.
    """

    name = "skills_catalog"
    priority = 40
    budget_bytes = 20000

    def prefetch(self, ctx: InjectContext) -> str | None:
        # 一键回滚开关 — 操作员撞 bug 时用
        always_inject = (
            os.environ.get("CATFISH_SKILLS_ALWAYS_INJECT", "0") == "1"
        )

        if not always_inject:
            # BL-MEMORY-INJECT-OPTIMIZE A: 条件 inject
            user_text = ctx.last_user_message
            if not _has_action_intent(user_text):
                logger.debug(
                    "skills_catalog: user message 无动作意图, skip inject 14KB. text=%r",
                    user_text[:50],
                )
                return None

        from ...skills_inject import _get_skills_block  # noqa: PLC0415

        try:
            block = _get_skills_block()
        except Exception as e:  # noqa: BLE001
            logger.warning("SkillsCatalogProvider _get_skills_block 失败: %s", e)
            return None
        if not block or not block.strip():
            return None
        return block
