"""prefetch 里那几段**静态文本** —— 从 catfish_memory_render.py 再拆一层 (8/15)。

跟隔壁 catfish_memory_render.py 的区别是**读不读盘**:

  · 本文件: 纯字符串模板 (小鲶的职责说明 / memory 工具 schema 决策树 /
    安全红线 / 事实优先纪律 / 记忆纪律 / skill 守则)。改这里就是改小鲶的
    行为准则, 不涉及任何数据。
  · 隔壁:   要读 ~/.catfish 下的文件再渲染 (skills / 战略文档 / 待办 /
    session_meta / wiki / journal / feedback)。

拆开是因为合在一起 849 行, 越过 CLAUDE.md §1 的 800 红线; 而这条"读不读盘"
的界线本来也是这堆方法里最清楚的一条。

同样是 mixin, 理由见 catfish_memory_render.py 的模块 docstring。
"""
from __future__ import annotations


class _SectionsMixin:
    """静态文本段。不读盘、不持状态。"""

    # ── 5 个数据源 render helper ──────────────────────────

    def _render_purpose(self) -> str:
        """BL-CATFISH-WIKI-MODE P0.1 (2026-06-03): 员工身份 + catfish 服务目的.

        借鉴 Karpathy LLM Wiki gist 的 purpose.md 概念 — 让 LLM 知道当前服务的
        员工是谁、做什么场景, 不再当 generic chatbot. llm_wiki 实现 (ingest.ts
        buildAnalysisPrompt) 真把 purpose 作 string 注入 prompt 末尾, 标 "for
        context", catfish 跟 _render_memory_discipline 同套路注入到 user message.

        # 为啥重要
        - LLM 不知道员工身份 → 回答时按 generic 写, 不带行业知识 / 公文体
        - 不知道服务场景 → 工具选错 (该走 catfish-weekly-report 时去 generic markdown)
        - 不知道员工偏好 → 周报里塞 catfish 个人开源项目 (已在 USER.md 修)

        # 内容来源
        鸿波亲笔写, 描述自己是谁 + 用 catfish 做啥 + 不做啥. 当前 v1 是占位 —
        鸿波下次拍板时可改 catfish-memory/purpose.txt (TODO P1 真接配置文件).
        """
        return (
            "## 🎯 员工身份与目的 (purpose)\n\n"
            "你服务的员工: **陈鸿波** (FFCS 数字鲶鱼项目发起人, 中电福富 + 法律部 / 企业发展与风控部).\n\n"
            "**主要工作场景**:\n"
            "- 企业资质管理 (高企 / ITSS / ISO 27001 / CMMI / CSMM / 数据安全)\n"
            "- 资质评估 + 申报 (含咨询公司参与决策)\n"
            "- 公司向上汇报 (周报 / 月度通报 / 立项材料 / 项目可研)\n"
            "- ISO 现场审核 / 评审准备\n"
            "- 跨部门沟通 (中电福富部门 + 九地市分公司)\n\n"
            "**员工偏好**:\n"
            "- 跳过铺垫直接交付结果, 拒绝估算 (要精确数据)\n"
            "- 偏好杂志风 / 花叔风 PPT, 公文体严谨\n"
            # 8/14: 目录名 output→outputs, 日期格式**写死** YYYY-MM-DD。
            #
            # 老写法是 `~/.catfish/output/<日期>/` —— `<日期>` 没规定格式, LLM 每次
            # 自己发挥, 于是员工机器上同时长出 output/20260813/ 和 output/2026-08-07/。
            # 这条提示词是**每轮都注入**的, 所以它是 LLM 产出目录乱飞的真正来源,
            # 比任何代码路径影响都大。给格式就不会飘。
            "- 文件输出到 ~/.catfish/outputs/<YYYY-MM-DD>/ (日期用这个格式, 别用别的), 不再问存哪\n"
            "- 个人开源项目 catfish/鲶鱼**不要**进周报 / 汇报 / 待办 (是个人事, 不是公司工作)\n\n"
            "**不该做的事**:\n"
            "- 不要自动跑周报 (员工每周手动提)\n"
            "- 不要绕 execute_code 审批走 terminal (见安全红线)\n"
            "- 不要乱写 MEMORY.md 当 skill spec 用 (见 memory 写入纪律)\n\n"
            "**禁止幻觉 (重要)**:\n"
            "- 引用员工历史 / 项目 / 决策 时, **必须**来自下面注入真 wiki / journal / "
            "USER PROFILE / MEMORY.md / SOUL.md. 不允许编造**没在注入数据里出现**的人 / "
            "项目 / 事件.\n"
            "- 没记录就**直接说 \"我没在你 catfish 记忆里找到这条\"**, "
            "不要靠 training prior 编 confabulation.\n"
            "- 真reference 真员工业务真 entity / concept 时**用真 `[[wiki title]]` "
            "精确链接 (见下面 P3.3 wiki summary 注入真 title list).\n"
        )

    def _render_schema(self) -> str:
        """BL-CATFISH-WIKI-MODE P0.2 (2026-06-03): catfish memory schema (AUTHORITATIVE).

        借鉴 llm_wiki buildGenerationPrompt 的 "Project Schema and Routing
        (AUTHORITATIVE)" 标记. schema 教 LLM 写到哪里去 (路由规则),
        memory_discipline 教写什么不该写 (内容纪律). 互补.

        P3.5.78 (6/22 鸿波 catch): 5 → 6 kind, 加 expense (收支记账). 真因 audit:
        6 月 22 日 P3.5.75 ship 独立 bookkeep plugin 后 LLM 看"19号加油300" 跑偏到
        journal — 因为 schema 决策树没收 expense, catch-all 第 6 条 "80% journal"
        把 LLM 锁死. 治本: expense 进 schema 第 6 kind, 跟 todo/journal 同款分流.
        """
        return (
            "## 📐 catfish memory schema (AUTHORITATIVE)\n\n"
            "**6 kind memory router** (调 `memory` tool 时 `kind` 必填):\n\n"
            "| kind | 路由到哪 | 用来存什么 |\n"
            "|---|---|---|\n"
            "| `identity` | `~/.hermes/memories/USER.md` (cap 3500 chars) | 员工本人 — 身份/偏好/习惯/昵称/关系 |\n"
            "| `project_fact` | `~/.hermes/memories/MEMORY.md` (cap 5000 chars) | 项目/技术常量 — 资质评估流程/工具配置/平台特征 |\n"
            "| `workflow` | hint → 调 `catfish_propose_skill` | 多步流程 — 有 step 序列的全部 |\n"
            "| `journal` | `~/.catfish/employee_journal.md` (append) | 本次会话总结 / 已发生事件 / pending TODO |\n"
            "| `todo` | hint → 调 `catfish_reminder_create` | 带 deadline 的任务 (会写 Reminders.app) |\n"
            "| `expense` | `~/.catfish/bookkeep.jsonl` (append) | 收支记账 — 员工说花/付/买/收/卖/加油/吃饭 + 金额 |\n\n"
            "**4 个长期存储分工**:\n\n"
            "- **USER.md (identity)** — 员工本人, 一年后还成立. 例: 偏好直接输出不要确认.\n"
            "- **MEMORY.md (project_fact)** — 项目/技术常量, 跨 session 稳定. 例: 资质评估流程.\n"
            "- **employee_journal.md (chronological)** — 时间线日志, append-only. 格式严格:\n"
            "  `## [YYYY-MM-DD HH:MM] kind | title` 一行 (parseable by `grep '^## \\['`).\n"
            "- **distilled_facts.md (LLM 蒸馏)** — 自动从 journal 蒸馏的长期记忆,\n"
            "  每 24h 由 catfish-memory plugin 跑. 员工只读不写.\n"
            "- **bookkeep.jsonl (expense)** — 收支流水, append-only. 每行 1 笔, schema:\n"
            "  `{id, ts, kind(支出/收入), amount, category, note}`. expense kind 调时填\n"
            "  `direction (支出/收入) + amount + category + note + date`.\n\n"
            "**SKILL.md (~/.hermes/skills/<name>/SKILL.md)** — 真正的 workflow / spec\n"
            "/ 触发词住这里, **不要**写进 MEMORY.md.\n\n"
            "**路由决策树** (调 memory 前自查):\n"
            "1. 员工本人的事? → identity → USER.md\n"
            "2. 项目/技术常量? → project_fact → MEMORY.md\n"
            "3. 多步流程? → workflow → 改调 catfish_propose_skill\n"
            "4. **金额数字 + 消费/收入动词** (花/付/买/收/卖/加油/吃饭/打车/工资)? → **expense → bookkeep.jsonl**\n"
            "5. 这次会话的事 / 已发生事件? → journal → employee_journal.md\n"
            "6. 带 deadline 的任务? → todo → 改调 catfish_reminder_create\n"
            "7. 拿不准 → 先看是不是 expense (金额数字+动词), 再 fallback journal\n"
        )

    def _render_safety_redline(self) -> str:
        """BL-LLM-NO-TERMINAL-BYPASS-V2 (2026-06-03): 安全红线 prompt.

        # 真触发场景
        6/3 下午员工 chat 真生产: LLM 真自己说 "execute_code 卡住, 我换个方案:
        直接用 terminal 调 pdftotext / python -c, 不经过 execute_code 审批流程."

        LLM 真自主越权信号 — 真 catfish 真所有代码执行 (Python / bash) 真该走
        execute_code → sandbox-exec / nsjail + 员工审批. terminal 真 hermes builtin
        默认 local 真直接 host 跑, 绕真审批 + sandbox.

        # 真为啥不 hook block (v1 撤回)
        hermes pre_tool_call hook 无 parent_tool 真字段, 真无法区分 LLM 直调
        vs catfish_run_skill / skill 内部真用 terminal. 真一刀切 block 误伤
        员工真合法 skill 路径. 改成注入红线 prompt 让 LLM 自查.

        # 真根治在别处
        - BL-TOOLS-SANITIZER-DROP-DEPRECATED (6/3 BACKLOG): 修 execute_code 真
          tool_call/tool_describe 死循环, LLM 真有正路可走真没动机绕.
        - audit log post_tool_call 真 LLM 直调 terminal 真触发警告 entry (后续).
        """
        return (
            "## 🚨 安全红线 (catfish 强约束)\n\n"
            "你**永远不要**用 `terminal` 工具跑代码 (Python / bash / shell).\n\n"
            "**理由**: terminal 真 hermes builtin 默认 local 直接 host 上跑命令, "
            "绕过 catfish 真 sandbox-exec / nsjail + 员工审批. catfish 真红线 — "
            "LLM 真所有代码执行必须走 sandbox + 员工 review.\n\n"
            "**正路**:\n"
            "- 跑代码: `execute_code(lang='python'|'bash', code='...')` → catfish sandbox\n"
            "- 读 PDF / Excel / docx: `execute_code` 真里调 pypdf / openpyxl / python-docx\n"
            "- 查文件: `read_file` / `glob` / `grep`\n"
            "- 浏览器自动化: `catfish_browser_*` 四件套 (goto/click/fill/snapshot)\n\n"
            "**禁用 terminal 真场景**:\n"
            "- ❌ 'execute_code 卡住, 改 terminal 绕过审批' — 主动越权, 拒\n"
            "- ❌ 'terminal 调 pdftotext 直接读' — 绕 sandbox, 拒\n"
            "- ❌ 'terminal 调 curl 拉数据' — 改 `execute_code(bash)` 或 `web_fetch`\n\n"
            "**唯一合法场景**: 员工自己真本机 shell 跑命令 (员工自己输, 不是你调).\n"
        )

    def _render_fact_first_discipline(self) -> str:
        """P3.5.211 (7/10 鸿波): 事实为准军规下沉到 hermes prefetch.

        # 触发场景
        7/10 鸿波审 CSMM-4 task chat: 员工原话只说 '准备迎接专家复审会资料, 预计
        7 月内会进行专家复审', AI 编:
          - '自评报告、运行记录、访谈提纲等' (员工没提材料清单)
          - '若组长还没给确切日期' (员工没说组长给没给, LLM 假设前提)
          - '建议本周内先拉内部团队过一遍材料, 模拟专家提问' (员工没提预演)

        # 为什么之前修的没生效
        改 Companion buildTaskSystemPrompt (P3.5.209/210) 100% 被 hermes 丢弃
        (hermes request dump 铁证). 军规必须放 hermes 内部 SystemPromptProvider
        走 prefetch 才能到 LLM.

        # 覆盖面
        影响所有走 hermes 的 chat: task chat / 工作台 chat / 主动闲聊 (proactive
        走 gateway 不受这里影响, 但 gateway proactive.py P3.5.206 已加同款).
        Advisor 走 gateway 也有 P3.5.206 SYSTEM_PROMPT 同款约束.
        """
        # P3.5.217 (7/10 鸿波 军规精简): 合并 P3.5.211/213/214/215/216 五版军规,
        # 从 2072 chars 压回 ~1000 chars. 只保留核心行为原则, 不再列优先级 /
        # 场景 / 冲突解决. 军规越长 attention 越稀释, LLM 抓不住重点. 5 版
        # 演进史:
        #   211 · 事实为准段, 用具体禁词作反面例子 → LLM negation blindness 复现
        #   213 · 移除具体锚点抽象化 → LLM 换成靠训练数据 recall 通识
        #   214 · 明确'想列名 → 调 tool', 不许凭训练知识补充
        #   215 · 加'信息缺口 3 步决策' (先搜后问再干)
        #   216 · 加'场景 A 新话题直接搜 / 场景 B 老话题授权问'
        # 217 精简策略: 3 条核心原则 + 优先级 + 冲突哲学. 短行短句 LLM 更抓得住.
        return (
            "## 📌 事实为准 (catfish 硬约束)\n\n"
            "**唯一事实源**: 员工 chat 原话 + tool 返回结果 + 早晨 briefing 上下文里字面出现.\n"
            "其它 (你训练数据里的行业通识 / 标准模板 / 通用清单) 都**不是事实**, "
            "是**你可能记错的知识**, 优先级 = 不存在.\n\n"
            "**3 条核心行为**:\n\n"
            "1. **想给员工看具体名** (材料 / 步骤 / 系统 / 日期 / 人名 / 清单) → "
            "**立刻调 tool** 拿 (catfish_local_search / catfish_email_search / "
            "catfish_run_skill / wiki). **tool 返什么写什么**, 别凭训练数据补充.\n"
            "   - 员工问 '下一步' 就是想快, **直接搜, 别问 '要不要帮你查'**. "
            "搜索无副作用, 只有推进外部动作 (发邮件/起草消息) 才用授权问句.\n"
            "   - Tool 没返 → 就说 '我搜过没找到', 别自己 recall 通识补上.\n\n"
            "2. **想推进动作** (发邮件 / 写报告 / 跑 skill) → 用**授权问句** "
            "('要不要我 X?'), 不写 '建议 X' / '你应该 X'. "
            "授权问句里也不能带 tool 没返过的具体名.\n\n"
            "3. **不确定 / 有前提 / 员工没说过的** → **直接问员工**, "
            "别用 '若...' 句式假设前提. 宁可少说, 不要多说. 你是参谋不是作文.\n\n"
            "**信息缺口 3 步顺序** (员工问 '下一步该干啥' 时按顺序判断):\n"
            "  1) 员工 wiki/journal 可能有 → **先 search** (少走这步 = 让员工觉得 '我明明自己有资料')\n"
            "  2) 需要外部信息 (对方给) → **起草询问** (微信/邮件) 让员工问外部\n"
            "  3) 员工可直接干 → **起草动作** (走 draft_email_reply / run_skill / execute_code)\n"
            "  顺序不能倒. 3 步不清晰时明说 '我判断是第 <N> 步, 因为 <理由>', 让员工纠正.\n"
        )

    def _render_memory_discipline(self) -> str:
        """BL-MEMORY-DISCIPLINE (5/24): hermes memory_update 写入纪律.

        # 真问题
        hermes 原生 memory_update tool 没硬约束, LLM 倾向于"对未来的我有用就写".
        结果鸿波实盘 USER.md + MEMORY.md 21 条 entry, **70% 跑偏**:
          - 5 条 skill 完整 spec (该进 ~/.hermes/skills/<name>/SKILL.md)
          - 6 条 session log / 已发生事件 (该进 ~/.catfish/employee_journal.md)
          - 3 条带 deadline 的具体任务 (该进 TODO)
          - 1 条 USER 内容写到了 MEMORY 名下 (员工身份 vs 项目知识混淆)
        累积导致 MEMORY.md 单 entry 撞 2401 chars (> 2200 cap), 仪表盘视觉满.

        # 这段干嘛
        在 system prompt 顶部硬注入"决策树", 让 LLM 调 memory_update 前自查 4 个反例.
        不强制 enforce (hermes 端无 hook), 靠 LLM 看到这段后改判定. 实战经验: prompt
        约束对 reasoning model 命中率 70%+.

        # 配套治理
        - 反向: ~/person_task/catfish/scripts/hermes-memory-cleanup.py (hm 脚本)
          员工每周手扫一次, 删 LLM 误写进去的.
        - 长期: catfish_memory_audit cron skill (未来)
        """
        return (
            "## ⚙ hermes memory 写入纪律 (catfish 强约束)\n\n"
            "调 `memory_update` (写 USER.md / MEMORY.md) 前必须自查:\n\n"
            "**✅ 该写的, 同时满足这 3 条:**\n"
            "1. 跨 session 稳定 — 一年后还成立 (员工身份/偏好/技术常量)\n"
            "2. 没有 deadline / 不会过期\n"
            "3. 不是 skill 的工作流, 不是会话总结, 不是单次任务状态\n\n"
            "**❌ 不该写的 (即使有 user 价值也别写 memory, 走下面对的地方):**\n"
            "- skill 完整 spec / 输出格式 / 触发词 / workflow → `~/.hermes/skills/<name>/SKILL.md`\n"
            "- 本次会话的总结 / 进度 / pending TODO → `~/.catfish/employee_journal.md`\n"
            "- 带具体日期的任务 (5/30 截止之类) → TODO 工具 / journal\n"
            "- 已发生事件的状态变更 (X 会议结束 / Y 已完成) → journal\n"
            "- 'next time when X is available, do Y' 类待办 → journal / issue\n"
            "- skill 创建/更新的事件记录 (filesystem 自己有) → 不写\n\n"
            "**target 怎么选:**\n"
            "- `target=user`: 关于员工**这个人**的事 (偏好/习惯/身份/关系)\n"
            "- `target=memory`: **项目/技术**事实 (API 字段含义、output 路径约定、客户机房 IP)\n"
            "- 拿不准 → 80% 概率属于 journal, 不属于 memory\n\n"
            "**长度纪律:**\n"
            "- USER.md 每 entry ≤ 1375 字符, MEMORY.md ≤ 2200. 接近上限的就拆 / 砍.\n"
            "- 写得超长的几乎都是把 spec / workflow 当 memory 写, 应改去 SKILL.md.\n"
        )

    def _render_skill_guard(self, query: str) -> str:
        """员工 query 提 skill 关键词时, 注入"用 catfish_run_skill" 铁律.

        简单 substring 触发: query 含 'skill' / '技能' / '跑技能' 等关键词才注入,
        其它 80% chat 不注入 (避免 prompt 噪音).
        """
        if not query:
            return ""
        q_lower = query.lower()
        keywords = ("skill", "技能", "跑技能", "做技能", "用技能", "调技能")
        if not any(k.lower() in q_lower for k in keywords):
            return ""
        return (
            "## ⚠ Skill Guard (catfish)\n\n"
            "员工提到 skill — 用 `catfish_run_skill` tool, 不要绕过. "
            "skill 是 catfish 自动化流水线 (e.g. 生成 ppt / 写 docx / 跑批),"
            "你自己写脚本=违规."
        )
