"""catfish-memory helpers — 抽自 catfish_memory.py (5/21 拆分).

包含: 文件 IO helper / journal 操作 / 蒸馏判定 / buffer 跟 state 持久化 /
plugin config 加载 / gateway URL 解析 / dev_token 获取 helpers.

主入口 CatfishMemoryProvider class 在 catfish_memory.py 内部 import 这些.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("catfish.memory")

#: 默认 catfish 数据目录 (员工本机). env CATFISH_HOME 覆盖.
#: 5/28 鸿波: helper 之前 reference 这个 constant 但**没定义**, 导致 is_available()
#: NameError, 整个 plugin 永远报 unavailable → hermes 报 "no provider instance found".
#: 这是 plugin 5/19 装好但 9 天一直没真注册的根因之二 (第一个根因是 __init__.py
#: import 链失败, 第二个就是这里).
_DEFAULT_CATFISH_HOME: Path = Path.home() / ".catfish"


def _catfish_home() -> Path:
    """`~/.catfish/` 或 env CATFISH_HOME 指定的目录."""
    env = os.environ.get("CATFISH_HOME")
    return Path(env).expanduser() if env else _DEFAULT_CATFISH_HOME


#: 每个数据源单独 budget (字节), 跟老 gateway provider 对齐.
#: 超出部分尾部截 (留前段更重要内容). 总 cap ~30KB, system prompt 容得下.
#: BL-STRATEGIC-DOC-SYNC (6/7): strategic_docs 子段 budget — 战略/设计 doc
#:   (manifesto / patent / moat 类). query 空走 8KB (cap 5 份 × ~1.5KB), query
#:   触发 top-K 时 _render_strategic_docs 内部 cap 到 5KB.
_BUDGETS: Dict[str, int] = {
    "employee_journal": 5000,
    # P3.5.5 (6/16 鸿波): skills_catalog 20K → 5K. 真因: 鸿波 advisor 流程 Qwen 内网
    #   prompt 44K 跑 100-200s. 真大头是 catfish-memory plugin prefetch 38.5KB 全量注入,
    #   单 skills_catalog 占 20K. 实测 chat 用 5K 够 (top-K 5 个 skill, 每 skill ~1K),
    #   员工 chat 时常用 skill 就那几个, 全列没必要. 砍 15K, advisor 提速 60%+.
    "skills_catalog": 5000,
    # P3.5.5 (6/16 鸿波): strategic_docs 8K → 3K. 战略 doc 是 manifesto / moat 类,
    #   员工 chat 时偶尔参考, 不需要全注入. 3K 够留 top-K 2 段.
    "strategic_docs": 3000,
    "feedback": 2000,
    "session_meta": 500,
    "skill_guard": 3000,
}


def _read_text_safe(path: Path, max_bytes: int) -> str:
    """读文件返字符串, 不存在 / IO 错 → 空字符串. 超 max_bytes 尾部截.

    永不抛, 让 prefetch 整体不挂.
    """
    try:
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        # 字节 cap — 简单按字符 truncate (UTF-8 可能切半字符, 凑合; 真要严谨
        # 用 incremental decoder, POC 不必).
        return text[: max_bytes // 3] + "\n...[truncated]"
    except OSError as e:
        logger.debug("catfish-memory: 读 %s 失败 %s, 跳过", path, e)
        return ""


def _read_jsonl_tail(path: Path, max_lines: int = 20, max_bytes: int = 2000) -> List[Dict[str, Any]]:
    """读 jsonl 最后 max_lines 条, 不存在 / 解析失败 → 空 list."""
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        recent = lines[-max_lines:]
        out: List[Dict[str, Any]] = []
        budget = max_bytes
        for raw in recent:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    out.append(obj)
                    budget -= len(raw)
                    if budget <= 0:
                        break
            except json.JSONDecodeError:
                continue
        return out
    except OSError as e:
        logger.debug("catfish-memory: 读 jsonl %s 失败 %s", path, e)
        return []


# ── 写路径 helpers (Week 2 — 替代 gateway session_summarizer + memory_distill) ──
#
# 这些 helper 都是**纯函数 + 显式参数**, 方便单测 mock 注入. 不读 module-level
# 全局状态 (除 env), 不依赖 CatfishMemoryProvider 实例.

#: gateway 旧 caller 跟我们这条 plugin 路径**双写**期间, plugin 写之前看 journal
#: mtime, < 这个秒数视为 gateway 刚写过, plugin skip (Step B-Step C 过渡期保护).
#: Step C env gate 关 gateway 后这层保护自然失效 (因为只有 plugin 自己在写).
_SUMMARIZE_DEDUP_SECONDS = 300  # 5 分钟

#: 蒸馏 24h cooldown (跟 gateway memory_distill 原 24h 一致, 防同次 chat 反复触发).
_DISTILL_COOLDOWN_SECONDS = 24 * 3600

#: sync_turn 节流默认: 每 N 轮触发一次 summary. env CATFISH_PLUGIN_SUMMARIZE_EVERY_N_TURNS 覆盖.
#: 跟 5/20 Step D 失败原因相关 — on_session_end 不在 per-chat trigger (run_agent.py:16078
#: 注释 "Memory provider on_session_end NOT called per turn"), 必须用 sync_turn + 节流.
_DEFAULT_TURNS_BETWEEN_SUMMARY = 5

#: sync_turn 节流默认: 距上次 summary 最小间隔 (秒). 跟 N 轮规则取 "或" — 任一满足都触发.
#: env CATFISH_PLUGIN_SUMMARIZE_MIN_INTERVAL_SECONDS 覆盖.
#: 30min 是经验值: 员工连续 chat 30min 算一段思路完成, 该总结了.
_DEFAULT_MIN_SUMMARY_INTERVAL_SECONDS = 1800

#: gateway loopback URL (env 覆盖, 默认 8999).
_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8999/v1/chat/completions"

#: HTTP 超时 (LLM 总结+蒸馏不应该超 60s; 真超就 cooldown 等下次).
_LLM_HTTP_TIMEOUT = 60.0

#: P1.1.1 wiki Step 2 Generation 单独超时 — 生 ~4000 tokens 长 response,
#: 60s 不够 (6/4 12:55 实测 ReadTimeout). 180s 给 LLM 慢慢生.
_GENERATION_HTTP_TIMEOUT = 180.0

#: 每个 session 取最多 N 条消息进 prompt (防长 session 撑爆 LLM context).
_MAX_MESSAGES_PER_SUMMARY = 60

#: 蒸馏 chunk 大小 (字符), 跟 gateway memory_distill 原 DISTILL_CHUNK_CHARS 对齐.
_DISTILL_CHUNK_CHARS = 8000

#: 总结 LLM prompt — 跟老 gateway 风格一致, 让员工 journal 风格连续.
_SUMMARIZE_PROMPT = (
    "你是企业员工的工作日志助手. 下面是员工跟 AI 副手的一次对话. "
    "用 1-2 个简短段落 (中文, ≤200 字) 总结这次对话的**关键决策、偏好、做出的事**. "
    "格式: 第一行 `### 主题`, 后面正文. "
    "**不要复述 AI 回答**, 只记员工立场 / 输出 / 偏好. "
    "如果没有实质内容 (比如员工只是闲聊或问候), 输出空字符串.\n"
)

#: 蒸馏 LLM prompt — 跟老 gateway memory_distill 一致 (抽人/项目/偏好/决策).
#: P3.5.203 (α 7/9 鸿波): 加"任务状态"段 — 5 层记忆 audit 后员工点出 Dream 蒸馏
#:   丢 task status 语义, chat/proactive/早安 主动推员工已说过关闭的事. 12x 压
#:   缩 (298KB → 24KB) 物理必然丢细节, 但 status 关键度高, LLM 应该单独保留.
#:   member 反馈 case: 员工 journal 写 "X 事暂时关闭", Dream 抽成 "X 项目" 归
#:   entities, status 丢. 加 "## 任务状态" 段让 LLM 明确抽 员工对具体事的最近表
#:   态, distilled_facts.md 里保留, prefetch 拼 system prompt 时 chat / advisor
#:   都拿得到. 输出格式跟 P3.5.202 (C 方案) LLM chatStatus 语义对齐:
#:     - resolved: 已办完/已交付/已确认误报/已撤销
#:     - paused: 暂时关闭/暂缓/先放放/等通知/先不管
#:     - pending: 球在员工手里, 继续跟进
_DISTILL_PROMPT = (
    "你是员工长期记忆蒸馏师. 下面是员工最近的工作日志. "
    "抽出**人物、项目、偏好、决策、任务状态**这 5 类关键事实.\n\n"
    "**输出格式** (5 段 markdown, 每段 1 个 `##` 标题):\n\n"
    "## 人物\n"
    "- ... (每条 1 行, ≤50 字)\n\n"
    "## 项目\n"
    "- ... (每条 1 行, ≤50 字)\n\n"
    "## 偏好\n"
    "- ... (员工的工作风格 / 决策倾向, 每条 1 行)\n\n"
    "## 决策\n"
    "- ... (员工做过的具体决定 + 日期, 每条 1 行)\n\n"
    "## 任务状态\n"
    "- <具体事> | resolved (员工说已办完/已交付/已确认误报/已撤销)\n"
    "- <具体事> | paused (员工说暂时关闭/暂缓/先放放/等通知/先不管)\n"
    "- 只列**员工明确表态过的事**, pending 状态的事不用列 (默认还在跟进).\n"
    "- resolved / paused 判定 = 员工原话有明确结案 / 主动搁置信号. 模糊的走\n"
    "  pending 不列, 保守不误判.\n\n"
    "**约束**:\n"
    "- 总长 ≤500 字 (5 段总和), 5 段都要有\n"
    "- 不复述原文, 只抽结论性事实\n"
    # P3.5.206 (7/9 鸿波军规 '所有生成内容都要以事实为准'): 蒸馏后 distilled_facts
    # 会喂 system prompt 影响后续所有 chat/advisor/briefing 输出, 蒸馏时若生动化/
    # 因果推断会一层层放大. 统一军规:
    "- **事实为准 (P3.5.206)**: 只用日志里字面出现的事实, 不做因果推断 / 价值判断\n"
    "  / 生动化修饰. 允许衔接词 (同时/然后/目前/另外), 禁结论词 (决定/因此/意味着/\n"
    "  影响/视为红线/直接影响).\n"
    "- 任务状态段是 P3.5.203 新加, 优先级高: 员工点扫描长期记忆时最关心'哪些事\n"
    "  我已经说过关掉了', 这段准 = chat / 早安主动 starter 不再撞员工已关闭的事.\n"
)

# ── BL-CATFISH-WIKI-MODE P1.1 (6/4) ──────────────────────
# 借 llm_wiki buildAnalysisPrompt + buildGenerationPrompt 拆 distill 为两步:
# Step 1 Analysis — 结构化抽 entities / concepts / decisions / contradictions
# Step 2 Generation — 每 entity/concept 生成 1 markdown page (frontmatter +
#                     ---FILE: <path>--- sentinel parser)
# 输出 ~/.catfish/wiki/entities/ + ~/.catfish/wiki/concepts/
# CATFISH_WIKI_ENABLE env toggle 默认 off (LLM 调用贵, 24h 1 次).

#: Step 1 Analysis prompt — 结构化抽 4 类. 中文优先 (员工日志中文为主).
#: P1.1.1 polish (6/4): 加员工偏好 skip rule catfish/鲶鱼 个人开源项目 不抽 —
#: 之前 catfish.md hallucinate 成"工作 system + CI/Security audit" 违 USER PROFILE.
_ANALYSIS_PROMPT = (
    "你是企业知识体系分析师. 下面是员工工作日志, 抽以下 4 类结构化信息.\n\n"
    "**重要 skip rule (优先级最高)**:\n"
    "- `catfish` / `鲶鱼` / `小鲶` / `胖胖` 这些名字 = 员工的 AI 副手 / 个人开源项目, "
    "**不是企业项目**, **不要抽成 entity**.\n"
    "- 员工的 chat 工具 / AI 助手相关 = 跨工具元话题, **不抽** (这些不构成业务知识).\n"
    "- 同样**不抽**: claude / hermes / openai / deepseek / qwen 等 AI 模型 / 工具名.\n\n"
    "**输出格式严格**:\n\n"
    "## Entities\n"
    "- <name> | <type: person/org/system/cert/project> | <一句话, ≤50字>\n"
    "- ... (≤6 条 entities, 按重要性排, 企业业务相关)\n\n"
    "## Concepts\n"
    "- <name> | <type: process/rule/principle/standard> | <一句话, ≤50字>\n"
    "- ... (≤6 条 concepts, **必须真至少 3 个** — 抽流程/规则/原则/标准)\n"
    "- 例: 资质评估流程 / 月度通报模板 / 文体规范 / 资质统筹原则 / 申报材料归档规范\n\n"
    "## Decisions\n"
    "- <YYYY-MM-DD> | <who> | <decided what> | <why>\n"
    "- ... (≤5 条, 最近)\n\n"
    "## Contradictions\n"
    "- <pair A vs B>: <冲突点, ≤80字>\n"
    "- ... (≤3 条, 没有就写 `(无)`)\n\n"
    "**约束**:\n"
    "- entity name 简短 (人名/机构缩写/产品名/资质名), 拼写跟员工原文一致\n"
    "- 不抽闲聊 / 待办 (待办在 journal 已有)\n"
    "- 不抽 catfish / AI 工具 / 大模型 (见上 skip rule)\n"
    "- 不复述原文, 只抽结论性事实\n"
    "- 中文优先, 必要时带英文 (e.g. ISO 27001)\n"
    "- 输出≤2000 字总\n"
)

#: Step 2 Generation prompt — 借 llm_wiki ---FILE: sentinel pattern.
#: 输入 = Analysis 输出, 输出 = 多 file markdown 拼接, 按 ---FILE: <path>--- 切分.
#: P1.1.1 polish (6/4): 1) **concepts 先 entities 后** 保 concepts 不被 token cap 吃;
#: 2) related YAML list 真 `["[[name1]]", "[[name2]]"]` 双引号 string list 兼容
#:    Obsidian + YAML 严格 (之前真 `[[[name]]]` 三括号双不兼容).
_GENERATION_PROMPT_TEMPLATE = (
    "你是企业知识体系作者. 下面是结构化分析结果 (Entities + Concepts + Decisions + "
    "Contradictions). 为**每个 entity 和 concept** 各生成 1 个 markdown 页, "
    "用 sentinel 切分.\n\n"
    # P3.5.176 (7/6 鸿波军规审判): 严格判 kind 用**语义原则**, 不 enum 死板.
    # 老 prompt 严格 concept_type=<process/rule/principle/standard> +
    # entity_type=<person/org/system/cert/project> 硬编码 enum → 员工场景
    # diverse (e.g. "组织架构", "市场经营体系") 严格 LLM 严格找不到匹配 enum
    # → 严格 kind 判错 (e.g. "市场部" 误 kind=concept subtype=system). 严格违反
    # AI-first 军规. 改语义原则驱动, LLM 自主判 kind + 自主填 subtype 短词.
    # P3.5.180 (7/6 鸿波军规审判): 删掉 P3.5.176 严格误导 example
    # "'市场部' 是具体部门 → entity" — 严格员工 mental model 严格 部门是
    # 组织架构 body 的层级单元, 严格不是独立 entity md. 严格 P3.5.176
    # example 严格 hardcode 引导 LLM 生 100+ 部门 entity, 严格违反 AI-first.
    # 严格 fix: 减 hardcode 严格不加 hardcode, 保 3 其他 example (组织架构 /
    # 数据分级规则 / 张三) 严格 LLM 自主判 部门 kind. 严格员工 diverse 场景
    # 严格 LLM 严格看 body 结构自主判 (若 body 严格层级树 严格作 concept body
    # 层级; 若独立个体 严格作 entity md).
    "**严格判 kind (语义驱动, 不列 enum 死板)**:\n"
    "- **concept** = 抽象类别 / 体系 / 规则 / 流程 (员工头脑里的**分类**, 复用率高)\n"
    "- **entity** = 具体存在物 (员工日常打交道的**具体对象**, 替换率高)\n"
    "- 员工场景 diverse 你自主判. 例: '组织架构' 是员工分类 → concept. "
    "'数据分级规则' 是抽象规则 → concept. '张三' 是具体人 → entity.\n\n"
    "**生成顺序**:\n"
    "- 先生 concepts (复用率高, token 紧张时不可丢)\n"
    "- 再生 entities (替换率高, token 紧张时可丢尾部)\n\n"
    "**输出格式严格**:\n\n"
    "```\n"
    "---FILE: wiki/concepts/<slug>.md---\n"
    "---\n"
    "type: concept\n"
    "title: <name>\n"
    # P3.5.176: concept_type 严格删 enum. LLM 自主填**贴切语义短词** (中文/英文
    # 员工可读). 顶级体系 (员工分类最高层) 严格约定 concept_type=system —
    # WikiTree isSystemConcept 严格识别显 '🌟 顶级体系' 分组, subtree BFS
    # 触发条件 (P3.5.108 6/25). 其他 concept_type 严格 LLM 自主 (e.g.
    # 'org-structure' / '资质分类' / 'process' 等 — 员工场景 diverse).
    "concept_type: <一个贴切语义的短词, 员工可读. 顶级分类体系约定填 'system'>\n"
    "created: {today}\n"
    "updated: {today}\n"
    "tags: [<tag1>, <tag2>]\n"
    # 8/4: 加上 typed 形式。读侧 6/29 (P3.5.132 #5) 就支持 {name, rel} 了, 但
    # **prompt 从头到尾没提过 rel** —— 实测 408 条边 0 条带类型, 不是 LLM 不配合,
    # 是根本没人要求过它。功能建在读侧、写侧不知道, 等于没建。
    "related: [{name: \"<名字>\", rel: \"<关系, 2-4 字>\"}, \"[[<关系拿不准就用这种>]]\"]\n"
    # P3.5.205 (7/9 鸿波 catch): sources 从常量 `[employee_journal]` 改**日志日期列表**,
    # 让员工能反查每 wiki 页来自哪几天日志. 格式: `[journal:YYYY-MM-DD, ...]` (取自
    # Analysis Decisions 段的 YYYY-MM-DD 字段, 或员工日志里 heading `## [ts] journal`
    # 的日期部分). 只列涉及该 entity/concept 的日期, 去重升序. 老 wiki 遇 `[employee_journal]`
    # 单值 → merge 时 LLM 按新格式升级.
    "sources: [\"journal:<YYYY-MM-DD>\", ...]\n"
    "---\n\n"
    "# <name>\n\n"
    "<3-5 段正文, 总 ≤600 字. 定义 / 适用场景 / 跟其它 concept 真区别 / 案例.>\n"
    "\n"
    # P3.5.205 (7/9 鸿波 catch KB '中电福富' 描述有生动化+因果推断偏差):
    # concept body 允许"衔接词" (同时/然后/目前/另外), 禁"结论词"
    # (决定/因此/意味着/影响/视为红线). 只 rephrase 日志字面事实, 不做
    # 二次因果推断 / 价值判断 / 生动化修饰. 员工看得像日志摘要, 不像
    # AI 生动作文.
    "**body 写作约束 (P3.5.205)**:\n"
    "- 只用日志字面出现的事实 rephrase, 不做因果推断 / 价值判断 / 生动化修饰\n"
    "- 允许衔接词: 同时 / 然后 / 目前 / 另外\n"
    "- 禁结论词: 决定 / 因此 / 意味着 / 影响 / 视为红线 / 直接影响\n"
    "\n"
    "## Related\n"
    "- [[<other entity/concept>]] — <为什么相关, ≤30字>\n"
    "- ... (≤4 条)\n"
    "\n"
    "---FILE: wiki/entities/<slug>.md---\n"
    "---\n"
    "type: entity\n"
    "title: <name>\n"
    # P3.5.176: entity_type 严格删 enum. LLM 自主填**贴切语义短词**. 员工场景
    # diverse (e.g. 'org' / 'person' / 'project' / '部门' / '证书' 等), 不 enum.
    "entity_type: <一个贴切语义的短词, 员工可读>\n"
    "created: {today}\n"
    "updated: {today}\n"
    # 8/4: aliases。跟 typed relation 一模一样的形状 —— 这个字段在 merge 侧
    # (_FM_LIST_FIELDS_UNION) 早就会合并了, 但**从没有人要求 LLM 产出过**,
    # 读侧也不按它解析。
    #
    # 代价实测: 97 个文件的 related 写的是「中电福富」, 而规范节点 title 是
    # 「中电福富信息科技有限公司」。前端只能退到 title 子串兜底, 而子串同时命中
    # 「销售许可证-中电福富API与应用系统安全审计V2.0」—— 指向谁取决于哪个文件
    # 最近被改过。别名把"这两个名字是同一个东西"变成写下来的事实。
    "aliases: [<简称>, <全称>, <英文名/旧称, 有才写>]\n"
    "tags: [<tag1>, <tag2>]\n"
    # 8/4: 加上 typed 形式。读侧 6/29 (P3.5.132 #5) 就支持 {name, rel} 了, 但
    # **prompt 从头到尾没提过 rel** —— 实测 408 条边 0 条带类型, 不是 LLM 不配合,
    # 是根本没人要求过它。功能建在读侧、写侧不知道, 等于没建。
    "related: [{name: \"<名字>\", rel: \"<关系, 2-4 字>\"}, \"[[<关系拿不准就用这种>]]\"]\n"
    # P3.5.205 (7/9 鸿波 catch): sources 从常量 `[employee_journal]` 改**日志日期列表**,
    # 让员工能反查每 wiki 页来自哪几天日志. 格式: `[journal:YYYY-MM-DD, ...]` (取自
    # Analysis Decisions 段的 YYYY-MM-DD 字段, 或员工日志里 heading `## [ts] journal`
    # 的日期部分). 只列涉及该 entity/concept 的日期, 去重升序. 老 wiki 遇 `[employee_journal]`
    # 单值 → merge 时 LLM 按新格式升级.
    "sources: [\"journal:<YYYY-MM-DD>\", ...]\n"
    "---\n\n"
    "# <name>\n\n"
    "<2-4 段正文, 总 ≤400 字. 1 段概述, 1 段关键关系/决策, 1 段贡献/角色.>\n"
    "\n"
    # P3.5.205 (7/9 鸿波): 同 concept body 约束, entity body 也走.
    "**body 写作约束 (P3.5.205)**:\n"
    "- 只用日志字面出现的事实 rephrase, 不做因果推断 / 价值判断 / 生动化修饰\n"
    "- 允许衔接词: 同时 / 然后 / 目前 / 另外\n"
    "- 禁结论词: 决定 / 因此 / 意味着 / 影响 / 视为红线 / 直接影响\n"
    "\n"
    "## Related\n"
    "- [[<other entity>]] — <为什么相关, ≤30字>\n"
    "- ... (≤4 条)\n"
    "```\n\n"
    "**约束**:\n"
    # 8/4: 老规则自相矛盾 —— 说"拼音**首字母**", 例子里 中电福富→zdff 是首字母,
    # 陈鸿波→chenhongbo 却是全拼。同一句话两套规则, LLM 只能猜, 实测产出第三种
    # (全拼且分词每次不同): zhongdianfufu / zhongdian-fufu / zhong-dian-fu-fu。
    # 结果是同一实体被拆成多个文件 (实测 21 组 / 50 个文件, 占全库 20%)。
    #
    # 改成**直接用中文 name**: 唯一、稳定、无分词歧义, 而且 wiki_write.rs 的
    # slugify 本来就保留中文, Companion UI 建的条目一直是中文名。1556 行的
    # 注释也早写了 "LLM 不遵守拼音, 直接用中文 name, Obsidian 支持 unicode slug"
    # —— 那就别再要求拼音了, 要求一个 LLM 做不到的事只会得到随机结果。
    "- slug = **直接用 name 本身**, 中文就写中文 (e.g. 中电福富 → 中电福富, "
    "ISO 27001 → iso-27001). 不要转拼音 —— 拼音分词不唯一, 同一实体会被拆成"
    "多个文件. 只把 / \\ : * ? 这类文件名非法字符换成连字符.\n"
    "- **先查已有条目**: 下面会给你现有 entity/concept 清单, 同一个东西**必须"
    "复用已有 slug**, 不要造新的变体.\n"
    "- entity slug 跟 concept slug 不冲突\n"
    "- frontmatter YAML 严格合法 (Obsidian 解析)\n"
    "- `aliases:` 写这个实体**在日志里实际出现过的其他叫法** —— 简称、全称、"
    "英文名、旧称。例: title 是「中电福富信息科技有限公司」就写 "
    "`aliases: [\"中电福富\", \"福富\"]`. "
    "**只写真出现过的**, 不要臆造缩写 —— 编出来的别名会把无关条目连到一起, "
    "比没有别名更糟。没有别名就写 `aliases: []`.\n"
    "- 别的条目引用它时, `related` 里用简称还是全称都行, 系统靠 aliases 认得出"
    "是同一个。\n"
    "- `related:` **优先带关系类型**: `{name: \"中电福富\", rel: \"隶属\"}`. "
    "rel 用 2-4 字中文短词 (隶属/认证/负责/参与/依赖/上级/同类). "
    "关系拿不准就退回裸 wikilink `\"[[名字]]\"` —— **编一个关系比没有关系更糟**.\n"
    "- `related:` 必须真双引号 string list — 正确: "
    "`related: [\"[[陈鸿波]]\", \"[[FFCS]]\"]`. "
    "**错**: `related: [[[陈鸿波]]]` (3 个 `[` YAML 真 inline list of list, "
    "Obsidian 不能 parse)\n"
    "- 每 file title 不重复\n"
    "- related wikilinks 真 `[[name]]` 必须指真 Analysis 里出现真 name\n"
    "- 同 slug 真 entity vs concept 真不允许 (按 type 分)\n"
    "- 全部输出 ≤6000 字\n"
)


def _build_generation_prompt() -> str:
    """注 {today} 真生成 prompt."""
    return _GENERATION_PROMPT_TEMPLATE.format(today=time.strftime("%Y-%m-%d"))


def _wiki_enabled() -> bool:
    """P1.1 wiki two-step 开关 — 优先 yaml, env 兜底, 默认 off (LLM 调用贵).

    P3.5.12 (6/16 鸿波): 加 yaml 守门, 优先级 yaml > env > default False.

    真因: 6/16 早 entities/concepts 真被自动写入 19+34 条, 鸿波反馈"对话自动入
    知识库会很乱". audit 证实: 当时 shell `CATFISH_WIKI_ENABLE=1` 被 hermes
    继承 → 此函数返 True → wiki two-step 跑. 老逻辑只读 env 不可靠 — env
    可能从任何 init script / launchd plist 传, 难根治.

    新逻辑: yaml `wiki.auto_ingest` 优先 (永久声明性配置, 跟 env 解耦).
    yaml 配 false → env 设 =1 也不动. yaml 不配 → fallback env (向后兼容).
    yaml + env 都没配 → default False.

    yaml 配法: ~/.catfish/memory_plugin.yaml 加段:
        wiki:
          auto_ingest: false   # 永远不自动入库, 员工 ChatBubble "💾 存 wiki" 手动入
    """
    # 优先级 1: yaml `wiki.auto_ingest` (单一权威配置)
    try:
        cfg = _load_plugin_config()
        if isinstance(cfg, dict):
            wiki_cfg = cfg.get("wiki", {})
            if isinstance(wiki_cfg, dict):
                ai = wiki_cfg.get("auto_ingest")
                if isinstance(ai, bool):
                    return ai
    except Exception:  # noqa: BLE001 - 配置读失败回退 env, 不挂 plugin
        pass

    # 优先级 2: CATFISH_WIKI_ENABLE env (向后兼容, 历史路径)
    val = os.environ.get("CATFISH_WIKI_ENABLE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _extract_message_pairs(messages: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """从 hermes message list 抽 (role, content) 对.

    跳过 system / tool / 空 content. 限 _MAX_MESSAGES_PER_SUMMARY 条 (尾部).
    """
    pairs: List[Tuple[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        pairs.append((role, content))
    if len(pairs) > _MAX_MESSAGES_PER_SUMMARY:
        pairs = pairs[-_MAX_MESSAGES_PER_SUMMARY:]
    return pairs


def _format_journal_entry(session_id: str, summary: str) -> str:
    """格式 journal entry. BL-CATFISH-WIKI-MODE P0.3 (6/3): 改用 Karpathy LLM Wiki
    log.md 风格 `## [YYYY-MM-DD HH:MM] kind | title`, 一行可 grep 解析.
    grep '^## \\[' employee_journal.md | tail -5 拉最近 5 条."""
    date_str = time.strftime("%Y-%m-%d %H:%M")
    # 8/4: 写**完整** session_id, 不再截成后 6 位。
    #
    # 老写法 `session_id[-6:]` 把溯源链掐断了: wiki 条目的 sources 只记到日期,
    # journal 只留 6 位后缀 —— 员工看到一条可疑的断言, 回溯不到说这句话的那次
    # 对话。而原始对话其实完整躺在 ~/.hermes/state.db (实测 5329 会话 /
    # 50794 条消息), 信息没丢, 只是指针被截断了。
    #
    # 不可验证 = 不可修正。知识库是四次 LLM 转写的产物 (summarize → analysis →
    # generation → merge), 失真是压缩的物理必然, 消除不了; 但"看到错的能查证"
    # 是可以做到的, 而且只差这一个字段。
    #
    # 存量条目靠后缀 LIKE 匹配仍能溯源 (session id 形如 20260803_195928_f39669,
    # 后 6 位同一天内基本唯一), 见 tool-bridge/wiki_trace.py。新条目从此精确。
    sid = session_id.strip() or "unknown"
    return f"## [{date_str}] session | {sid}\n\n{summary.strip()}\n"


def _append_journal(catfish_home: Path, entry: str) -> None:
    """追加一段 entry 到 catfish_home/employee_journal.md. 自动建父目录."""
    path = catfish_home / "employee_journal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = entry.strip() + "\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(body)


def _read_picker_state_model(catfish_home: Path) -> str:
    """P3.5.2 (6/16 鸿波): 读 ~/.catfish/picker_state.json 拿 companion chat picker 当前 model.

    ⚠⚠ 【原则, 8/4 鸿波明确拍板】蒸馏必须用**员工自己选的那个模型**, 不许换。

    8/4 讨论知识库准确性时提过一个"优化": 蒸馏一天只跑几次, 换个更强的模型成本
    可忽略, 质量还更好 —— 加个 distill_model 独立配置就行。鸿波否掉了, 明确说
    这是原则问题。

    别再提这个"优化"。员工选了哪个模型, 知识库就该是那个模型的产物 —— 换成别的
    模型偷偷生成, 员工看到的东西就不再是他能预期的东西了。质量差是可以接受的,
    不透明不行。

    (下面的注释是原始设计说明, 保留。)

    companion chat.ts 每次 send 前 fire-and-forget 写这个文件, atomic write.
    plugin sync_turn 触发时读, 让 summary model 自动跟随 picker (而不是 yaml 静态).

    设计 (方案 B, 6/16 鸿波拍): hermes MemoryProvider.sync_turn 签名没 picker 入参,
    plugin 拿不到 picker 状态. 文件中转是绕过 hermes API 限制的最简方案.

    优先级 (caller _get_summarize_model): picker_state.json > yaml > env > 空.

    Args:
        catfish_home: ~/.catfish 目录

    Returns:
        picker model 字符串. 文件不存在 / parse 错 / chat_model 字段缺 → 空字符串.
        Caller 看到空就走 fallback (yaml/env).
    """
    path = catfish_home / "picker_state.json"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            model = data.get("chat_model", "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(
            "catfish-memory: read picker_state.json 失败 (fallback yaml/env): %s", e,
        )
    return ""


def _read_full_journal(catfish_home: Path) -> str:
    """全文读 employee_journal.md 给蒸馏用 (不走 5KB inject 截断). 没文件 → 空."""
    path = catfish_home / "employee_journal.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ── BL-CATFISH-WIKI-MODE P1.2.3 (6/4) — wiki/queries/ 触发 partial ingest ──

def _wiki_ingested_state_path(catfish_home: Path) -> Path:
    """记 哪些 wiki/queries/*.md 已被 ingest, 避免重复处理."""
    return catfish_home / "wiki_ingested_state.json"


def _read_wiki_ingested_state(catfish_home: Path) -> Dict[str, float]:
    """返 {file_basename: ingested_ts} dict."""
    p = _wiki_ingested_state_path(catfish_home)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _mark_wiki_queries_ingested(catfish_home: Path, file_names: List[str]) -> None:
    """append 已 ingest 真 queries file 名 + ts 到 wiki_ingested_state.json."""
    if not file_names:
        return
    state = _read_wiki_ingested_state(catfish_home)
    now = time.time()
    for name in file_names:
        state[name] = now
    p = _wiki_ingested_state_path(catfish_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("写 wiki_ingested_state.json 失败: %s", e)


def _list_pending_queries(catfish_home: Path) -> List[Path]:
    """列 wiki/queries/ 真未 ingest 真 *.md file (按 mtime 排, 最旧先)."""
    queries_dir = catfish_home / "wiki" / "queries"
    if not queries_dir.is_dir():
        return []
    state = _read_wiki_ingested_state(catfish_home)
    pending = []
    try:
        for f in queries_dir.glob("*.md"):
            if f.name not in state:
                pending.append(f)
    except OSError:
        return []
    # 按 mtime 排 (旧 → 新)
    try:
        pending.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        pass
    return pending


def _read_queries_concat(query_files: List[Path], max_chars: int = 12000) -> str:
    """读所有 queries file 拼一段 text 给 Analysis. 总 cap max_chars 防爆.

    格式: 每 file 加 `### query: <filename>` 头. content 整 file (含
    frontmatter — Analysis LLM 能看 metadata).
    """
    if not query_files:
        return ""
    parts = []
    used = 0
    for f in query_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"### query: {f.name}\n\n{text.strip()}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


# ── P16 (6/5 鸿波) — wiki/raw/sources/ 触发 partial ingest ─────────────────
# 对话上传文件 → ~/.catfish/wiki/raw/sources/<ts>-<slug>.md (含 frontmatter
# + 全文 body, Companion wiki_ingest_source `Tauri command 写真). 这
# 一组 helper 跟 P1.2.3 queries hook 同结构, 复用 wiki_ingested_state.json
# (key 加 `source:` 前缀防与 queries 冲突).
# sync_turn 3b 会把 sources + queries 一起 merge 进 Analysis input → LLM
# 抽 entity/concept → wiki/entities/ + wiki/concepts/.


def _list_pending_sources(catfish_home: Path) -> List[Path]:
    """列 wiki/raw/sources/ 真未 ingest 真 *.md file (按 mtime 排, 最旧先)."""
    sources_dir = catfish_home / "wiki" / "raw" / "sources"
    if not sources_dir.is_dir():
        return []
    state = _read_wiki_ingested_state(catfish_home)
    pending = []
    try:
        for f in sources_dir.glob("*.md"):
            key = f"source:{f.name}"
            if key not in state:
                pending.append(f)
    except OSError:
        return []
    try:
        pending.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        pass
    return pending


def _read_sources_concat(source_files: List[Path], max_chars: int = 24000) -> str:
    """读所有 sources file 拼一段 text 给 Analysis. 总 cap max_chars 防爆.

    Sources 全文体积比 queries 大 (PDF/Word 转出来), max_chars 默认 24K
    (queries 12K 真 2 倍). 还是会被 cap, 单文件超 24K 会 break.

    格式: 每 file 加 `### source: <filename>` 头. content 整 file 含
    frontmatter (Analysis LLM 能看 filename / kind / uploaded date).
    """
    if not source_files:
        return ""
    parts = []
    used = 0
    for f in source_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"### source: {f.name}\n\n{text.strip()}\n"
        if used + len(block) > max_chars:
            # 超 cap 但还想塞点 — 截前 (max_chars - used) 字进去 + 标记截断
            remain = max_chars - used
            if remain > 500:
                parts.append(block[:remain] + "\n\n[... source 内容截断, 剩余下次 ingest]")
                used = max_chars
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def _mark_wiki_sources_ingested(catfish_home: Path, file_names: List[str]) -> None:
    """append 已 ingest 真 sources file 名 + ts 到 wiki_ingested_state.json.
    key 加 `source:` 前缀防与 queries 冲突 (queries 用裸 file_name).
    """
    if not file_names:
        return
    state = _read_wiki_ingested_state(catfish_home)
    now = time.time()
    for name in file_names:
        state[f"source:{name}"] = now
    p = _wiki_ingested_state_path(catfish_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("写 wiki_ingested_state.json (sources) 失败: %s", e)


def _should_run_distill(catfish_home: Path) -> bool:
    """24h 内跑过 → False (不再跑). 没跑过 / 已超 24h → True."""
    state_path = catfish_home / "memory_distill_state.json"
    if not state_path.exists():
        return True
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        last = state.get("last_run_ts", 0)
        return (time.time() - last) >= _DISTILL_COOLDOWN_SECONDS
    except (OSError, ValueError, json.JSONDecodeError):
        return True


def _mark_distill_run(catfish_home: Path) -> None:
    """写 memory_distill_state.json 记录这次跑过."""
    state_path = catfish_home / "memory_distill_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run_ts": time.time(),
        "last_run_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "catfish-memory-plugin",
    }
    try:
        state_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        logger.warning("写 memory_distill_state.json 失败: %s", e)


def _write_distilled(catfish_home: Path, text: str) -> None:
    """覆盖写 distilled_facts.md (跟老 gateway memory_distill.write_distilled_facts 等价)."""
    path = catfish_home / "distilled_facts.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- Generated by catfish-memory plugin at "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} -->\n"
        f"<!-- on_session_end 触发, 来源 catfish-memory plugin -->\n\n"
    )
    try:
        path.write_text(header + text.strip() + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("写 distilled_facts.md 失败: %s", e)


_GATEWAY_URL_DEPRECATION_LOGGED = False


def _gateway_url() -> str:
    """gateway loopback URL.

    P23 (6/5 鸿波): 统一 env 名 `CATFISH_GATEWAY_URL` (base URL, 不带 path) 跟
    Companion / catfish-xcatfish-user plugin 对齐. 老名 `CATFISH_GATEWAY_INTERNAL_URL`
    保留兼容 (含 full path), 设了 → 打 deprecation warning 用一次提醒.

    P24 (6/5 鸿波): yaml 接入 — ~/.catfish/memory_plugin.yaml 加 `gateway.url`
    字段 (跟 env 同义, 但客户端友好 — 不用 launchctl 改).

    优先级 (高→低):
      1. CATFISH_GATEWAY_INTERNAL_URL env (老, full URL e.g. http://x/v1/chat/completions)
      2. CATFISH_GATEWAY_URL env (新统一名, base URL), 自动拼 /v1/chat/completions
      3. yaml gateway.url (base URL), 自动拼 /v1/chat/completions
      4. _DEFAULT_GATEWAY_URL (http://127.0.0.1:8999/v1/chat/completions)
    """
    global _GATEWAY_URL_DEPRECATION_LOGGED
    full = os.environ.get("CATFISH_GATEWAY_INTERNAL_URL", "").strip()
    if full:
        if not _GATEWAY_URL_DEPRECATION_LOGGED:
            logger.warning(
                "P23 deprecation: CATFISH_GATEWAY_INTERNAL_URL 老 env 名, "
                "改用 CATFISH_GATEWAY_URL (base URL, 不带 path). 这次先兼容."
            )
            _GATEWAY_URL_DEPRECATION_LOGGED = True
        return full
    base = os.environ.get("CATFISH_GATEWAY_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/v1/chat/completions"
    # P24 yaml 兜底
    cfg = _load_plugin_config()
    yaml_base = ""
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            yaml_base = str(gw.get("url", "")).strip().rstrip("/")
    if yaml_base:
        return f"{yaml_base}/v1/chat/completions"
    return _DEFAULT_GATEWAY_URL


# ── 文件持久化 buffer + state (BL-MEMORY-SYNC-TURN-REFACTOR Day 2, 5/20) ───
#
# 发现 (5/20 12:30 实测): hermes api_server 模式**每个 chat completion request
# 创建新 AIAgent + 新 plugin instance**. 我们 plugin 内部 instance state
# (_turn_buffer / _turns_since_last_summary / _last_summary_ts) **每次重置**,
# 节流计数器永不累积到 5.
#
# Trace 证据 (5 次同 session_id curl): 5 个不同 instance id
#   112013d10 → 111fa5710 → 1120058d0 → 112022390 → 111fda910
#   counter_before 全 0.
#
# 修法: 节流 state 跨 instance 持久化到文件, plugin 每次 sync_turn 读 file
# 状态做节流判断, 触发后写 file 清空. 文件锁 (fcntl.flock) 防并发写.

#: buffer 文件 — 跨 instance 累积 user/assistant pairs, jsonl 一行一 entry
_BUFFER_FILENAME = ".catfish_memory_buffer.jsonl"

#: state 文件 — 跨 instance 存 last_summary_ts (单 dict json)
_STATE_FILENAME = ".catfish_memory_state.json"


def _buffer_file_path(home: Path) -> Path:
    return home / _BUFFER_FILENAME


def _state_file_path(home: Path) -> Path:
    return home / _STATE_FILENAME


def _read_buffer(home: Path) -> List[Tuple[str, str]]:
    """读 buffer file 返 list of (role, content). 不存在/corrupt 返空."""
    path = _buffer_file_path(home)
    if not path.exists():
        return []
    pairs: List[Tuple[str, str]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            # shared lock for read (best-effort; macOS/Linux fcntl)
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except (OSError, ImportError):
                pass
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    role = obj.get("role")
                    content = obj.get("content")
                    if isinstance(role, str) and isinstance(content, str):
                        pairs.append((role, content))
                except (json.JSONDecodeError, AttributeError):
                    continue
    except OSError:
        return []
    return pairs


def _append_to_buffer(home: Path, role: str, content: str, session_id: str) -> int:
    """append entry to buffer file. 返新 pair 数 (len(entries) // 2)."""
    path = _buffer_file_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": time.time(),
        "role": role,
        "content": content,
        "session_id": session_id,
    }, ensure_ascii=False)
    try:
        with path.open("a", encoding="utf-8") as f:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except (OSError, ImportError):
                pass
            f.write(entry + "\n")
    except OSError:
        return 0
    # count by re-reading (cheap, file 通常 < 20 entries)
    return len(_read_buffer(home))


def _clear_buffer(home: Path) -> None:
    """清空 buffer file (删除)."""
    path = _buffer_file_path(home)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _read_state(home: Path) -> Dict[str, Any]:
    """读 state file. 不存在/corrupt 返空 dict."""
    path = _state_file_path(home)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def _write_state(home: Path, state: Dict[str, Any]) -> None:
    """覆盖写 state file."""
    path = _state_file_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# ── plugin 配置 yaml (BL-PLUGIN-CONFIG-YAML 5/20 Day 2.5) ─────────
#
# 鸿波拍板: 配置不硬编码 / env, 走 yaml 参数文件, 跟 ~/.catfish/companion.yaml
# 同套路 (catfish 全栈共享配置位置).
#
# 文件路径: ~/.catfish/memory_plugin.yaml
#
# 优先级: yaml > env > hardcoded default. env 兜底兼容老部署.
#
# 内容示例:
#   enabled: true
#   summarize:
#     model: catfish-private-vision
#     every_n_turns: 5
#     min_interval_seconds: 1800

_PLUGIN_CONFIG_FILENAME = "memory_plugin.yaml"


def _plugin_config_path(home: Optional[Path] = None) -> Path:
    """yaml 配置文件位置. 默认 ~/.catfish/memory_plugin.yaml.

    home 参数让单测可指定 fake home; 没传时用 _catfish_home() (env CATFISH_HOME aware).
    """
    return (home or _catfish_home()) / _PLUGIN_CONFIG_FILENAME


def _load_plugin_config(home: Optional[Path] = None) -> Dict[str, Any]:
    """读 yaml 配置. 不存在 / 解析失败返空 dict (走 env / default 兜底).

    PyYAML 不可用时也返空 (优雅降级) — env 仍 work.
    """
    path = _plugin_config_path(home)
    if not path.exists():
        return {}
    try:
        import yaml  # 懒 import, PyYAML 是 hermes 自带依赖
    except ImportError:
        logger.debug("PyYAML 不可用, plugin yaml config 不加载")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("catfish-memory plugin yaml 配置加载失败 (%s): %s", path, e)
        return {}


def _read_hermes_env_key(key: str) -> str:
    """读 hermes 管理的 env key. 双查 os.environ + ~/.hermes/.env 文件.

    BL-PLUGIN-AUTH-FIX (7/27 鸿波): 为什么必须双查 —
      hermes `hermes_cli/config.py:load_env()` 只**返回 dict 不写 os.environ**.
      写 os.environ 只发生在 `/reload` 命令 (reload_env():8163) 或
      `set_env_value():8046`. 所以 plugin 光 os.environ.get() 可能拿不到.
      hermes 自己的 `get_env_value():8186` 就是双查 (先 os.environ 后 .env 文件),
      本函数语义跟它对齐.

      不 import hermes_cli.config — plugin 不该耦合 hermes 内部模块 (跨版本易断).
      Companion Rust 侧 `dream.rs:read_hermes_dev_env()` 也是直读 .env 文件, 同思路.

    parse 规则跟 dream.rs:262-278 对齐: 跳空行/注释, strip 引号.
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        env_path = Path(os.path.expanduser("~")) / ".hermes" / ".env"
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}="):
                continue
            raw = line[len(key) + 1:].strip()
            # strip 成对引号 (跟 dream.rs strip_env_quotes 对齐)
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                raw = raw[1:-1]
            return raw.strip()
    except OSError as e:
        logger.debug("catfish-memory 读 ~/.hermes/.env 失败 (%s): %s", key, e)
    return ""


_TOKEN_MISSING_MSG = (
    "catfish-memory: 拿不到 gateway 鉴权 token, %s skip. "
    "查顺序 ① ~/.hermes/.env OPENAI_API_KEY (hermes→gateway service token, "
    "跑 central/llm-gateway/refresh-jwt-hermes-env.sh 刷新) "
    "② env/. env CATFISH_INTERNAL_DEV_TOKEN ③ ~/.catfish/memory_plugin.yaml gateway.token"
)


def _log_token_missing(what: str) -> None:
    """BL-PLUGIN-AUTH-FIX (7/27): 统一 fail-loud. 老代码 4 处静默 return None,
    员工永远不知道 distill/wiki 从没跑过 (Dream Engine 9.7 天没跑就是这么来的).
    只记日志不弹 UI (后台任务失败不该打扰员工 · 鸿波 7/27 拍)."""
    logger.warning(_TOKEN_MISSING_MSG, what)


def _gateway_dev_token() -> str:
    """拿调 gateway 的鉴权 token. 没拿到返空 (caller skip + fail-loud log).

    BL-PLUGIN-AUTH-FIX (7/27 鸿波 catch "Dream Engine 9.7 天没跑"):

    ── 真因 ──
    老实现只读 CATFISH_INTERNAL_DEV_TOKEN. 但那是 **gateway 进程内 loopback 专用**
    (central/llm-gateway/.../auth/dev_token.py:11-15 明写 "启动时随机生成, 进程内存,
    重启即变, 不写 .env 文件"), 真 caller 只有 gateway 自己的 proactive.py /
    conversation_compressor.py. plugin 跑在 **hermes 进程** (另一进程 · 生产还跨机),
    永远拿不到 → _call_distill_llm 静默 return None → Dream Engine 自动蒸馏从没跑过.

    ── 正解 ──
    plugin 在 hermes 进程内, 调的又是 gateway, 就该复用 **hermes → gateway 这一跳**
    的凭证 = .env `OPENAI_API_KEY`:
      - aud=catfish-gateway · token_use=service · scope 含 chat.completions
      - 由 hermes-cli client_credentials 派发 (scope 经 identity 白名单校验, 可信)
      - refresh-jwt-hermes-env.sh 30 天刷新 (已有维护机制)
      - 生产分离时 OPENAI_BASE_URL 指中央 · 这 token 也是中央派发 · **天然跨机**

    链路: Companion ─[API_SERVER_KEY]→ hermes:8642 ─[OPENAI_API_KEY]→ gateway:8999 → LLM
                                          └── 本 plugin (in-process, 复用第 2 跳凭证)

    优先级:
      1. OPENAI_API_KEY (hermes→gateway service token · 生产正路)
      2. CATFISH_INTERNAL_DEV_TOKEN (本机 dev · gateway 同机且手工预设过时用)
      3. yaml gateway.token (P24 6/5 手工预设兜底)
    """
    token = _read_hermes_env_key("OPENAI_API_KEY")
    if token:
        return token
    token = _read_hermes_env_key("CATFISH_INTERNAL_DEV_TOKEN")
    if token:
        return token
    cfg = _load_plugin_config()
    if isinstance(cfg, dict):
        gw = cfg.get("gateway", {})
        if isinstance(gw, dict):
            return str(gw.get("token", "")).strip()
    return ""


async def _call_summarize_llm(
    pairs: List[Tuple[str, str]], model: str,
) -> Optional[str]:
    """调 gateway loopback /v1/chat/completions 总结一次. 失败返 None.

    跟老 session_summarizer._summarize_with_llm 行为等价:
      - X-Catfish-Skip-Identity: 防 gateway 给这次内部调用又注入 SOUL/journal
      - X-Catfish-Internal: 跳 quota check
      - Authorization Bearer <dev_token>
      - temperature 0.3, max_tokens 600 (短总结)
    """
    if not pairs:
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("summarize (sync_turn 会话总结)")
        return None

    # 拼上下文
    context_lines = [f"[{role}]: {content[:500]}" for role, content in pairs]
    user_prompt = _SUMMARIZE_PROMPT + "\n\n会话历史:\n\n" + "\n\n".join(context_lines)

    try:
        import httpx  # 懒 import, plugin 装时已经依赖 hermes 全套
    except ImportError:
        logger.warning("catfish-memory: httpx 不可用, summarize skip")
        return None

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 600,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory summarize: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = text.strip()
            return text or None
    except Exception as e:  # noqa: BLE001
        logger.warning("catfish-memory summarize 异常: %s", e)
        return None


async def _call_distill_llm(
    journal_text: str, model: str,
    progress_cb=None,
) -> Optional[str]:
    """调 gateway 蒸馏老 journal. 失败返 None.

    跟 memory_distill.maybe_run_llm_distillation 行为等价 (简化版):
      - 切 _DISTILL_CHUNK_CHARS 大小 chunk
      - 每 chunk 走 gateway 抽人/项目/偏好/决策
      - 全部失败返 None, 部分成功合并返

    P3.5.1.1 (6/15 鸿波 Dream Engine): 加可选 progress_cb(done_idx, total) —
      Dream Engine UI 进度条用. 每 chunk 跑前调一次 (done_idx 从 0 开始 = "马上跑第 1 段"),
      全部跑完再调一次 (done=total). 现有 sync_turn/on_session_end caller 不传 = None,
      不影响行为. progress_cb 抛错被吞 (诊断 UI 挂不该拖累 distill).
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("distill (Dream Engine 长期记忆蒸馏)")
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 简单切 chunk (按字符, 不按 ## 段边界 — POC 阶段够用; 老 gateway 切段边界更精细)
    chunks: List[str] = []
    remaining = journal_text
    while remaining:
        chunks.append(remaining[:_DISTILL_CHUNK_CHARS])
        remaining = remaining[_DISTILL_CHUNK_CHARS:]
    if not chunks:
        return None

    total = len(chunks)

    def _notify(done: int) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(done, total)
        except Exception as e:  # noqa: BLE001
            logger.debug("distill progress_cb 抛错 (吞掉, 不影响 distill): %s", e)

    results: List[str] = []
    async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
        for idx, chunk in enumerate(chunks):
            _notify(idx)
            try:
                resp = await client.post(
                    _gateway_url(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Catfish-Skip-Identity": "true",
                        "X-Catfish-Internal": "true",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": _DISTILL_PROMPT + "\n\n" + chunk}],
                        "temperature": 0.2,
                        "max_tokens": 1000,
                        "stream": False,
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "catfish-memory distill chunk HTTP %d, skip 这段",
                        resp.status_code,
                    )
                    continue
                data = resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                text = text.strip()
                if text:
                    results.append(f"### 蒸馏段 {len(results) + 1}\n\n{text}")
            except Exception as e:  # noqa: BLE001
                logger.debug("catfish-memory distill chunk 异常 (跳过): %s", e)
                continue

    _notify(total)  # 跑完通知一次

    if not results:
        return None
    return "\n\n".join(results)


# ── BL-CATFISH-WIKI-MODE P1.1 wiki two-step ─────────────────────

async def _call_analysis_llm(
    journal_text: str, model: str,
) -> Optional[str]:
    """Step 1 Analysis: 结构化抽 entities/concepts/decisions/contradictions.

    输入 = 全 journal_text (≤ _DISTILL_CHUNK_CHARS 真 chunk).
    输出 = markdown 结构化 (4 个 ## 段) 或 None (失败).

    比 _call_distill_llm 真区别: 输出结构化 (parseable), 不是 bullet list.
    """
    if not journal_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki analysis (Step 1 实体抽取)")
        return None
    try:
        import httpx
    except ImportError:
        return None

    # 单 chunk 走 (journal 真大时切前 _DISTILL_CHUNK_CHARS — 最新优先 tail).
    chunk = journal_text[-_DISTILL_CHUNK_CHARS:] if len(journal_text) > _DISTILL_CHUNK_CHARS else journal_text

    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": _ANALYSIS_PROMPT + "\n\n" + chunk}],
                    "temperature": 0.2,
                    "max_tokens": 2500,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory analysis: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw Step 1 output 到 file. 跑通后删.
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_analysis.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish-memory analysis 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


def _existing_wiki_index(catfish_home: Path, limit: int = 400) -> str:
    r"""列现有 entity/concept 的 `slug ← title`, 拼成给 Generation LLM 的清单。

    8/4: prompt 里要求"同一个东西必须复用已有 slug", 那就得**真的把清单给它** ——
    在此之前 _call_generation_llm 只喂 analysis, LLM 完全不知道已有什么, 每次
    从零造 slug, 于是同一实体攒出一堆拼音变体 (实测 21 组 / 50 个文件)。

    写盘那道 _redirect_to_existing_equivalent 是确定性兜底 (只认规范化等价);
    这份清单是让 LLM 一开始就少造变体, 两者互补, 都不能省:
      · 清单降低发生率, 但 LLM 不保证遵守
      · 兜底保证同名变体一定合并, 但拦不住"中电福富" vs "中电福富信息科技有限公司"
        这种语义重复 —— 那个只有 LLM 看见清单才可能避免
    """
    lines = []
    for sub, kind in (("wiki/entities", "entity"), ("wiki/concepts", "concept")):
        d = catfish_home / sub
        if not d.is_dir():
            continue
        try:
            for p in sorted(d.iterdir()):
                if p.suffix != ".md":
                    continue
                try:
                    head = p.read_text(encoding="utf-8", errors="replace")[:400]
                except OSError:
                    continue
                title = ""
                for line in head.splitlines():
                    if line.strip().startswith("title:"):
                        title = line.split("title:", 1)[1].strip()
                        break
                lines.append(f"{kind}/{p.stem} ← {title or p.stem}")
        except OSError:
            continue
    if not lines:
        return ""
    truncated = len(lines) > limit
    body = "\n".join(lines[:limit])
    return (
        "## 现有条目 (同一个东西必须复用这里的 slug, 不要造新变体)\n\n"
        + body
        + ("\n… (还有更多, 已截断)" if truncated else "")
    )


async def _call_generation_llm(
    analysis: str, model: str, catfish_home: Optional[Path] = None,
) -> Optional[str]:
    """Step 2 Generation: 把 analysis 转 wiki pages (---FILE: sentinel).

    输入 = Step 1 真 analysis text (~2000 字).
    输出 = ---FILE: <path>--- 切分真 multi-file markdown 或 None.
    """
    if not analysis.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki generation (Step 2 条目生成)")
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        # P1.1.1 fix (6/4): generation 单独 180s timeout — 4096 tokens 生 LLM
        # 60s 不够 (12:55 ReadTimeout 实测).
        async with httpx.AsyncClient(timeout=_GENERATION_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user",
                         "content": _build_generation_prompt()
                         + (("\n\n" + _existing_wiki_index(catfish_home)) if catfish_home else "")
                         + "\n\n## Analysis\n\n" + analysis}
                    ],
                    "temperature": 0.3,
                    # P1.1.1 fix (6/4): 7000 → 4096 兼容 deepseek-flash /
                    # catfish-private-main 真 output cap. Generation 真
                    # ~3 concept + ~5 entity 估 3500 tokens 够.
                    "max_tokens": 4096,
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory generation: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            # P1.1.1 debug (6/4): 写 raw LLM output 到 file 看 sentinel 不符原因.
            # 跑通后删 (BL-CATFISH-WIKI-MODE P1.1.2 cleanup).
            try:
                _debug_dump = Path.home() / ".catfish" / "last_wiki_generation.txt"
                _debug_dump.write_text(text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return text.strip() or None
    except Exception as e:  # noqa: BLE001
        # P1.1.1 fix (6/4): str(e) 空时 type(e).__name__ + repr 真 hint —
        # 之前 'generation 异常: ' 空 message 直接看不出真什么 error.
        logger.warning(
            "catfish-memory generation 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


# ============================================================
# P19 (6/5 鸿波) — LLM merge mode: 同名 entity/concept 让 LLM 真合并叙述,
# 不是 P18 `body 替换 + 旧 body 注释留底` `(留底法)`.
#
# 触发时机: _write_wiki_files 检测重名 → 上层 sync_turn 3b 在 await
# _call_generation_llm 后, 调 _call_merge_llm 替换 file dict `重名 entry`.
# LLM 失败 → fallback 走 _merge_wiki_file (P18 regex merge 当安全网).
#
# Prompt 设计: 给 LLM 两版 (OLD + NEW) `整 markdown` 真, 让它生 merged
# 完整 markdown (frontmatter + body). frontmatter 规则 LLM 自己读 prompt,
# body `不直接拼接, 而是合一个连贯叙述, 矛盾的标 OLD/NEW 两段.
# ============================================================

_MERGE_PROMPT_TEMPLATE = (
    "你是企业知识体系维护员. 下面是同一个 wiki 页 (entity 或 concept) 真两个版本:\n"
    "OLD (现有 wiki, 已存) 和 NEW (基于新 source 生成).\n"
    "你的任务: 合并真一个最终版.\n\n"
    "**合并规则**:\n\n"
    "frontmatter:\n"
    "- title: 用 NEW (允许改名)\n"
    "- created: 用 OLD (保留身份历史, 不丢)\n"
    "- updated: {today}\n"
    "- entity_type / concept_type: 用 NEW (允许 reclassify)\n"
    "- tags / related / sources / aliases: 并集去重 (旧 ∪ 新)\n\n"
    "body:\n"
    "- **不直接拼接** 两版段落; 合一个连贯叙述\n"
    "- 重复信息只说一次\n"
    # P3.5.205 (7/9 鸿波 catch KB 中电福富 时间线误判): 老规则只有 OLD/NEW 二态,
    # LLM 把'并行事件'(07-08 高企申报进入盖章 + 07-09 同时启动 ITSS)误判成
    # '替换关系'. 改三态:
    #   1) 明确替换 → OLD/NEW (岗位变更, 事实纠错, 战略切换)
    #   2) 并行 (两版都对, 各说一件) → "另外 / 同时" 平铺
    #   3) 澄清 (新版更细化) → 直接换掉旧模糊表述, 不留 OLD 标注
    # 默认走并行/澄清 (不写 OLD/NEW), 只有肯定是替换关系才写 OLD/NEW.
    "- **三态判断 (P3.5.205)**:\n"
    "  1. 明确替换 (岗位变更, 事实纠错, 战略切换): 标 'OLD: 之前 X' / 'NEW: 现在 Y', 注日期\n"
    "  2. 并行 (两版都对, 各说一件): '另外' / '同时' 平铺, 不写 OLD/NEW\n"
    "  3. 澄清 (新版更细化): 直接换掉旧模糊表述, 不留 OLD 标注\n"
    "  - **默认走 2 或 3**, 只有肯定是替换关系才用 1\n"
    # P3.5.205: body 收紧同 generation prompt
    "- **不做因果推断 / 价值判断 / 生动化修饰**: 只用日志字面事实 rephrase\n"
    "- 允许衔接词 (同时/然后/目前/另外), 禁结论词 (决定/因此/意味着/影响/视为红线/直接影响)\n"
    "- 保留 NEW 所有新事实, OLD 只丢与 NEW 矛盾或过期部分\n"
    # P3.5.205: sources 升级 — 若 OLD 是常量 [employee_journal], 用 NEW 的日期列表升级
    "- **sources 升级**: 若 OLD `sources: [employee_journal]` (老格式), 用 NEW `[journal:YYYY-MM-DD, ...]` 替换. 若两版都新格式 → 并集去重升序.\n"
    "- 总字数: entity ≤500 / concept ≤700\n"
    "- 文末加 `## 变更历史` section, 1 行 bullet:\n"
    "  `- {today}: 基于 <source> 更新, 主要变化: <一句话>`\n\n"
    "**输出**: ONLY 最终 markdown (含 frontmatter + body), 无其他说明 / 解释 / 引号.\n"
    "frontmatter `related:` 字段必须 `[\"[[name]]\", ...]` 双引号 string list.\n\n"
    "=== OLD (已存 wiki) ===\n"
    "{old_text}\n"
    "=== END OLD ===\n\n"
    "=== NEW (基于新 source 生) ===\n"
    "{new_text}\n"
    "=== END NEW ==="
)


def _build_merge_prompt(old_text: str, new_text: str) -> str:
    return _MERGE_PROMPT_TEMPLATE.format(
        today=time.strftime("%Y-%m-%d"),
        old_text=old_text.strip(),
        new_text=new_text.strip(),
    )


async def _call_merge_llm(
    old_text: str, new_text: str, model: str,
) -> Optional[str]:
    """Step 3 Merge (P19, 6/5): 同名 wiki 真两版让 LLM 合并真一版.

    输入: 旧 markdown (含 frontmatter) + 新 markdown (LLM 真生成的 NEW).
    返: merged markdown 或 None (LLM 失败 → caller fallback regex merge).

    timeout 60s — 单 entity merge prompt 短, 比 generation 快.
    """
    if not old_text.strip() or not new_text.strip():
        return None
    token = _gateway_dev_token()
    if not token:
        _log_token_missing("wiki merge (条目合并)")
        return None
    try:
        import httpx
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=_LLM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _gateway_url(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Catfish-Skip-Identity": "true",
                    "X-Catfish-Internal": "true",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user",
                         "content": _build_merge_prompt(old_text, new_text)},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,  # single entity merge, 4096 没必要
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "catfish-memory merge: HTTP %d (%s), skip",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = text.strip()
            # 校验: 必须 `---\n` 开头 (frontmatter 存在), 否则 LLM 没遵守输出格式
            if not text.startswith("---\n"):
                logger.warning(
                    "catfish-memory merge: LLM output 不以 frontmatter 开头, skip"
                )
                return None
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "catfish-memory merge 异常 [%s]: %r",
            type(e).__name__, e,
        )
        return None


#: P19 LLM merge 的验收阈值 —— 只拦明显事故, 不做美学判断。
_MERGE_MIN_BODY_RATIO = 0.4     # 正文缩到旧+新较长者的 40% 以下 = LLM 偷懒


def _accept_llm_merge(
    rel_path: str,
    old_text: str,
    new_content: str,
    merged: str,
) -> Optional[str]:
    r"""验收 P19 LLM merge 的输出。不合格返 None → 自动落回 P18 regex 安全网。

    # 为什么要验收 (8/4)

    P19 把整个文件交给 LLM 重写, 拿到就写盘, **一行校验都没有**。而它只在
    **更新已有条目**时触发 —— 一个条目被更新 N 次就被 LLM 重写 N 次, 误差累积。

    8/4 在鸿波机器上实测到的后果: 255 条里 19 条 frontmatter 丢了开头的 `---`,
    读侧一个字段都读不到, title 退化成文件名 (UI 上显示成拼音), tags/related
    全部失效 —— 那些条目在知识图谱里是没有任何连线的孤岛。

    P18 反而更安全: frontmatter list 取并集、created 保留旧、旧 body 转
    `<!-- legacy body -->` 注释留在文末。**确定性, 且信息不丢。** 它唯一的
    缺点是叙述不如 LLM 融合的顺 —— 那是可以接受的代价。

    所以不砍 P19, 给它加验收: 过了就用 (读起来更好), 没过就退回 P18 (更保守)。
    回退机制本来就有 (merged 为假 → n_failed → 不进 ok_paths → 写盘走 P18),
    这里只是把"LLM 调用失败"扩展成"LLM 输出不合格"。

    # 三条检查, 都来自实际事故形态, 不是想象

    1. frontmatter 必须完整 (`---` 开头 + 有收尾)
       → 8/4 那 19 个坏文件就是这个形态

    2. 旧 frontmatter 的 list 字段不能丢
       related / tags / sources 在 P18 里是**并集**语义。LLM merge 丢掉 related
       → 图里连线消失、实体掉进"未分类"。丢了就是倒退, 不接受。

    3. 正文不能暴缩 (< 旧/新 较长者的 40%)
       LLM 偷懒把长文压成一句话。P18 至少有 legacy 留底, P19 是直接覆盖。

    阈值保守是故意的: 宁可放过一些不完美的合并, 也不要频繁退回 P18 让叙述碎掉。
    每次拒绝都打 WARNING —— 静默退回等于把 LLM 跑偏这件事藏起来。
    """
    if not merged or not merged.strip():
        return None

    # ① frontmatter 完整性
    fm_new, body_new = _split_frontmatter_body(merged)
    if not fm_new:
        logger.warning(
            "catfish-memory P19 拒收 %s: 输出没有完整 frontmatter "
            "(不以 --- 开头, 或缺收尾) —— 落回 P18 regex merge",
            rel_path,
        )
        return None

    # ② 旧 list 字段不能丢 (并集语义, 丢了就是倒退)
    fm_old, body_old = _split_frontmatter_body(old_text)
    if fm_old:
        old_lists = _parse_frontmatter_lists(fm_old)
        new_lists = _parse_frontmatter_lists(fm_new)
        for key, old_vals in old_lists.items():
            if not old_vals:
                continue
            lost = [v for v in old_vals if v not in new_lists.get(key, [])]
            if lost:
                logger.warning(
                    "catfish-memory P19 拒收 %s: 合并后 %s 丢了 %d 项 (%s) —— "
                    "这些字段是并集语义, 丢了会让条目从图谱/筛选里消失. 落回 P18",
                    rel_path, key, len(lost), ", ".join(lost[:3]),
                )
                return None

    # ③ 正文不能暴缩
    _, body_incoming = _split_frontmatter_body(new_content)
    baseline = max(len(body_old.strip()), len(body_incoming.strip()))
    if baseline > 0 and len(body_new.strip()) < baseline * _MERGE_MIN_BODY_RATIO:
        logger.warning(
            "catfish-memory P19 拒收 %s: 正文从 %d 缩到 %d 字符 "
            "(不足 %.0f%%) —— 疑似 LLM 偷懒压缩. 落回 P18 (有 legacy 留底)",
            rel_path, baseline, len(body_new.strip()), _MERGE_MIN_BODY_RATIO * 100,
        )
        return None

    return merged


async def merge_files_with_llm(
    catfish_home: Path,
    files: Dict[str, str],
    model: str,
) -> Tuple[Dict[str, str], set, int]:
    """P19: 遍历 LLM 生的 file dict, 重名走 LLM merge.

    返 (final_files, ok_paths_set, n_failed).
    - ok_paths_set: 真 LLM merge 成功的 rel_path 集合 — 传给 _write_wiki_files
      真 skip_merge_paths, 跳过 P18 regex merge (因 LLM 已合).
    - 失败的 entry `保持原 LLM 生成 content (新版)`, 落到 P18 regex 安全网.
    """
    out = dict(files)
    ok_paths: set = set()
    n_failed = 0
    for rel_path, new_content in files.items():
        target = catfish_home / rel_path
        if not target.exists():
            # 新建, 无需 merge
            continue
        try:
            old_text = target.read_text(encoding="utf-8")
        except OSError:
            continue
        # 8/4: 员工改过的条目连送都不送给 LLM。
        # 送了再拒是浪费 token, 而且只要送出去就有被改写的可能 —— 边界该划在这里,
        # 不是划在验收上。写盘那边会走 _append_as_appendix 把新内容放进附录。
        if _is_employee_authored(old_text):
            logger.info(
                "catfish-memory P19 跳过 %s: 员工改过的条目, 不交给 LLM 重写", rel_path
            )
            continue
        try:
            merged = await _call_merge_llm(old_text, new_content, model)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "catfish-memory merge_llm %s 异常 [%s]: %r",
                rel_path, type(e).__name__, e,
            )
            merged = None
        # 8/4: LLM 的输出要过验收才采用, 不合格当失败处理 → 自动落回 P18。
        merged = _accept_llm_merge(rel_path, old_text, new_content, merged)
        if merged:
            out[rel_path] = merged
            ok_paths.add(rel_path)
            logger.info(
                "catfish-memory wiki LLM merge ✓ %s (%d → %d chars)",
                rel_path, len(new_content), len(merged),
            )
        else:
            n_failed += 1
    return out, ok_paths, n_failed


# 路径白名单 — 防 LLM 输出真 ---FILE: 逃逸 wiki/ 根.
# P1.1.1 fix (6/4): \w + re.UNICODE 让 slug 接受中文 (LLM 不遵守拼音, 直接用中文 name —
# Obsidian 也支持 unicode slug, 没必要强制 ASCII).
_WIKI_PATH_PATTERN = __import__("re").compile(
    r"^wiki/(entities|concepts)/[\w][\w_-]*\.md$",
    __import__("re").UNICODE,
)

# sentinel pattern 真 ---FILE: <path>--- 行.
_FILE_SENTINEL = __import__("re").compile(r"^---FILE:\s*(.+?)\s*---\s*$", __import__("re").MULTILINE)


def _parse_generation_output(text: str) -> Dict[str, str]:
    """切 LLM 输出按 ---FILE: <path>--- sentinel, 返 {rel_path: content} dict.

    路径白名单 _WIKI_PATH_PATTERN — 只允 wiki/entities/<slug>.md /
    wiki/concepts/<slug>.md. 其它 path silent skip (LLM 真乱写 / 真逃逸防护).
    """
    if not text:
        return {}
    # 找所有 sentinel 真 (path, start_offset) 真
    matches = list(_FILE_SENTINEL.finditer(text))
    if not matches:
        return {}

    files: Dict[str, str] = {}
    for i, m in enumerate(matches):
        rel_path = m.group(1).strip()
        # 白名单验
        if not _WIKI_PATH_PATTERN.match(rel_path):
            logger.debug("catfish-memory wiki parse: skip 非白名单 path %r", rel_path)
            continue
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        # 真 strip trailing fence 真 ``` (LLM 真有时 wrap markdown)
        if content.endswith("```"):
            content = content[:-3].rstrip()
        if content.startswith("```"):
            # 真 strip first line 真 ``` / ```markdown 之类
            content = content.split("\n", 1)[-1].lstrip()
        if not content:
            continue
        files[rel_path] = content
    return files


_FM_LIST_FIELDS_UNION = ("tags", "related", "sources", "aliases")
_FM_LIST_RE = re.compile(r"^([a-z_]+):\s*\[(.*?)\]\s*$", re.MULTILINE)
_FM_SCALAR_RE = re.compile(r"^([a-z_]+):\s*(.+?)\s*$", re.MULTILINE)


def _split_frontmatter_body(text: str) -> Tuple[str, str]:
    """切 markdown 真 (frontmatter_yaml, body). 没 frontmatter 返 ('', text)."""
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end < 0:
        return "", text
    return text[4:end], text[end + 5 :]


def _split_top_level(inner: str) -> list[str]:
    r"""按顶层逗号切分, 不切进 {} 和 [[]] 里面 —— Rust split_top_level 的 Python 版。

    8/4: typed relation `{name: "中电福富", rel: "隶属"}` 里**有逗号**。老实现是
    "regex 抓所有 quoted 字符串", 于是切出 ['中电福富', '隶属', ...] —— 关系标签
    「隶属」变成一个假节点名, 合并时会被当成关联写回 frontmatter, 图谱上凭空多出
    dangling 边。

    读侧 (wiki_read.rs::split_top_level) 一直是 brace-aware 的; 写侧不是。
    **又是两侧不同口径** —— 而且这次是在我准备让 prompt 开始产出 typed relation
    的前一刻才查出来的, 差一点就上线了。
    """
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    in_str = False
    quote = ""
    for c in inner:
        if in_str:
            buf.append(c)
            if c == quote:
                in_str = False
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            buf.append(c)
        elif c in "{[":
            depth += 1
            buf.append(c)
        elif c in "}]":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


_REL_NAME_RE = re.compile(r'name\s*:\s*["\']?([^"\',}]+)')


def _rel_item_name(item: str) -> str:
    """拿一条 related 的**节点名** —— 两种形态都认。

        {name: "中电福富", rel: "隶属"}  → 中电福富
        "[[北京福富]]"                   → 北京福富
        中电福富                          → 中电福富

    并集去重要按名字, 不能按整个字符串: 同一个节点带不带 rel 是同一条边,
    按字符串去重会留下两份。
    """
    v = item.strip()
    if v.startswith("{"):
        m = _REL_NAME_RE.search(v)
        return m.group(1).strip() if m else ""
    return v.strip('"').strip("'").strip().strip("[]").strip()


def _parse_frontmatter_lists(fm: str) -> Dict[str, List[str]]:
    """从 YAML frontmatter 抠 list 字段 ([\"[[a]]\", \"b\"]). 简单 regex,
    不全 YAML, 但对 prompt `生成` 真 format 够用.

    fix (6/5 测): 之前 regex `([^,]+)` 把 `, ` `分隔符` 也 match` 当 item
    → tags 重复. 改用先 split 再清, 简单稳.
    """
    out: Dict[str, List[str]] = {}
    for m in _FM_LIST_RE.finditer(fm):
        key = m.group(1)
        if key not in _FM_LIST_FIELDS_UNION:
            continue
        inner = m.group(2).strip()
        if not inner:
            out[key] = []
            continue
        # 先 split by `,` (不在 `[[..]]` 内), 再 strip quotes
        # 简化: regex 抓所有 quoted (带 [[..]] 或纯字符串) 优先, 否则裸 token
        # 8/4: 改用 brace-aware 切分 (见 _split_top_level 注释)。
        # 老实现是"regex 抓所有 quoted", typed relation 里的 rel 值会被当成
        # 独立条目 —— 关系标签变假节点。
        raw_items = _split_top_level(inner)
        items = []
        for it in raw_items:
            if it.startswith("{"):
                items.append(it)                    # typed, 整块保留
            else:
                items.append(it.strip('"').strip("'").strip())
        items = [s for s in items if s and s not in ("[", "]")]
        out[key] = items
    return out


def _parse_frontmatter_scalar(fm: str, key: str) -> Optional[str]:
    """抠 scalar 字段 (created / type / title 这种). 跳过 list ([..])."""
    for m in _FM_SCALAR_RE.finditer(fm):
        if m.group(1) == key:
            val = m.group(2).strip()
            if val.startswith("["):
                continue
            return val.strip('"').strip("'")
    return None


def _merge_wiki_file(old_text: str, new_text: str) -> str:
    """P18 (6/5 鸿波) — 重名 entity/concept merge 法 (纯 Python, 不烧 LLM).

    策略:
      frontmatter list 字段 (tags/related/sources/aliases): 旧 ∪ 新 (去重保序)
      frontmatter scalar:
        created: 保留旧 (entity `真`身份历史不丢`**)
        updated: 用新 (今天日期)
        title / *_type: 用新 (允许 reclassify)
      body: 用新, 旧 body 转 HTML 注释 `<!-- legacy body (created=<旧updated>) -->`
            放文末, 便于人工对照. 多次 update `只保留最近一份 legacy`.
    """
    old_fm, old_body = _split_frontmatter_body(old_text)
    new_fm, new_body = _split_frontmatter_body(new_text)
    if not new_fm:  # 新 file 没 frontmatter → 异常, 直接返新 (上层 fallback)
        return new_text

    old_lists = _parse_frontmatter_lists(old_fm)
    new_lists = _parse_frontmatter_lists(new_fm)

    # 1. list 字段并集替换到 new_fm
    merged_fm = new_fm
    for field in _FM_LIST_FIELDS_UNION:
        # 8/4: 按**节点名**去重, 不按整串 —— 同一个节点带不带 rel 是同一条边。
        # 两边都有时保留带 rel 的那个 (信息更多)。
        union: list[str] = []
        seen_names: dict[str, int] = {}
        for v in list(old_lists.get(field, [])):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen_names:
                if v.startswith("{"):
                    union[seen_names[n]] = v
                continue
            seen_names[n] = len(union)
            union.append(v)
        for v in new_lists.get(field, []):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen_names:
                if v.startswith("{"):
                    union[seen_names[n]] = v        # 新的带 rel → 升级
                continue
            seen_names[n] = len(union)
            union.append(v)
        if not union:
            continue
        # 双引号包每个 item (跟 Generation prompt 规范一致)
        quoted = ", ".join(
            v if (v.startswith("{") or v.startswith('"')) else f'"{v}"'
            for v in union
        )
        new_line = f"{field}: [{quoted}]"
        # 替已存的 list 字段; 没的话不动 (let new_fm 自然的没)
        merged_fm = re.sub(
            rf"^{field}:\s*\[.*?\]\s*$",
            new_line,
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 2. created 保留旧 (新生成的 created=今天, 改回旧)
    old_created = _parse_frontmatter_scalar(old_fm, "created")
    if old_created:
        merged_fm = re.sub(
            r"^created:\s*.+$",
            f"created: {old_created}",
            merged_fm,
            count=1,
            flags=re.MULTILINE,
        )

    # 3. body: 新 + 旧 legacy 注释
    old_updated = _parse_frontmatter_scalar(old_fm, "updated") or "unknown"
    stripped_old = old_body.strip()
    if stripped_old:
        # 防嵌套: 如果旧 body 已含 legacy 注释, 抽 inner 替, 不层叠
        inner_old = re.sub(
            r"<!--\s*legacy body \(.*?\)\s*-->\n?(.*?)\n?<!--\s*/legacy\s*-->",
            "",
            stripped_old,
            flags=re.DOTALL,
        ).strip()
        if inner_old:
            legacy_block = (
                f"\n\n<!-- legacy body (last updated={old_updated}) -->\n"
                f"{inner_old}\n"
                f"<!-- /legacy -->\n"
            )
            new_body = new_body.rstrip() + legacy_block

    return f"---\n{merged_fm}\n---\n{new_body}"


_FM_KEY_LINE = re.compile(r"^[a-z_]+:\s")
_FM_FENCE_LINE = re.compile(r"^---\s*$", re.MULTILINE)


def _ensure_frontmatter_fence(rel_path: str, content: str) -> str:
    r"""写盘前兜底: frontmatter 少了开头那行 `---` 就补上。

    # 为什么需要 (8/4 鸿波 "知识库里很多条目是拼音")

    读侧 (companion wiki_read.rs:81 split_frontmatter) 的规则很硬:

        let trimmed = text.trim_start();
        if !trimmed.starts_with("---") { return (String::new(), text); }

    不以 `---` 开头 → 整个 frontmatter 当正文, **一个字段都读不到**。后果不是
    "格式略丑":
      · title 读不到 → fallback 成文件名 → UI 上显示成拼音 slug
      · tags 读不到  → 标签筛选里消失
      · related 读不到 → 知识图谱里没有任何连线, 实体树掉进"未分类"

    8/4 在鸿波机器上实测: 255 条里有 19 条是这样, 文件里明明写着
    `title: 高新资质申报`, UI 上一直显示 `gaoxin-zizhi-shenbao`。而且全程零报错
    —— 读侧遇到这种文件是"正常返回一个 title=slug 的条目", 不是失败。

    # 为什么修在这里

    _write_wiki_files 是三条写盘路径的唯一收口, 而三条里只有一条保证了 `---`:

      新建            → content 是 _parse_generation_output 的 LLM 原始输出, 无校验
      已存在 + P19    → content 是 _call_merge_llm 的**原样回复**, 完全无校验
      已存在 + P18    → _merge_wiki_file 用 f"---\n{fm}\n---\n{body}" 重建 ✓

    P19 那条最容易犯: prompt 要求 LLM 输出完整 markdown, 拿到就
    `out[rel_path] = merged` 写盘。LLM 少打一行 `---` 就毁一个条目, 而它只在
    **更新已有条目**时触发 —— 坏掉的正好都是老条目。

    与其在三处各补一遍, 不如钉在收口。

    # 判据

    只在"看起来是丢了开头 fence"时才补, 不瞎改:
      1. 开头不是 `---`
      2. 且第一行长得像 YAML 键 (`^[a-z_]+:\s`)
      3. 且后面存在一行独立的 `---` (本该是收尾 fence)

    三条都满足才动手。没有 frontmatter 的纯正文 markdown 是合法的, 不碰。

    补的同时打 WARNING —— 这是上游 LLM 输出跑偏的信号, 静默修好等于把问题
    藏起来, 下次换个形式再犯。
    """
    if content.lstrip().startswith("---"):
        return content
    stripped = content.lstrip("\n")
    first_line = stripped.split("\n", 1)[0]
    if not _FM_KEY_LINE.match(first_line):
        return content          # 纯正文, 本来就没 frontmatter
    if not _FM_FENCE_LINE.search(stripped):
        return content          # 连收尾 fence 都没有, 不是这一类, 别猜
    logger.warning(
        "catfish-memory wiki: %s 的 frontmatter 缺开头的 --- (LLM 输出跑偏), "
        "已补上。不补的话读侧一个字段都读不到, title 会退化成文件名。",
        rel_path,
    )
    return "---\n" + stripped


#: P3.5.205/206 在 prompt 里禁的"结论词" —— 现在真的去查一遍。
#: 只查, 不改内容: 这些词出现不代表一定错, 但它是"LLM 在做因果推断/价值判断"
#: 的信号, 而知识库要的是日志字面事实。
_CONCLUSION_WORDS = (
    "决定", "因此", "意味着", "视为红线", "直接影响",
)


def _scan_conclusion_words(rel_path: str, content: str) -> list[str]:
    r"""扫正文里的禁用结论词, 命中就打 WARNING。返回命中的词。

    # 为什么加这个 (8/4 鸿波 "要怎么提高准确性")

    prompt 里写了一整套准确性约束 (P3.5.205 / P3.5.206):

        - 只用日志字面出现的事实 rephrase, 不做因果推断 / 价值判断 / 生动化修饰
        - 允许衔接词: 同时 / 然后 / 目前 / 另外
        - 禁结论词: 决定 / 因此 / 意味着 / 影响 / 视为红线 / 直接影响

    但**没有一条是可执行的检查** —— 全靠 LLM 自觉。写进 prompt ≠ 生效, 这跟
    8/3~8/4 查了一整天的那些静默失败是同一个形状: 规则写了, 没人验, 于是跑偏
    了也没有任何东西会喊一声。

    # 为什么只警告不拦

    这些词出现不等于内容错 —— 员工原话里就可能有"决定"。硬拦会丢数据, 而且
    这是文风判断, 不该由一个词表说了算。

    它的价值是**让漂移可见**: 日志里出现这类 WARNING 变多, 说明蒸馏 prompt
    的约束正在失效 (换了模型 / prompt 被改 / 上游 hermes 变了), 该去查了。
    在此之前, 这种漂移是完全无声的。

    注意 "影响" 没进词表: 它在中文里太常见 ("影响范围" / "受影响的系统"),
    误报会淹掉真信号。词表宁可漏, 不可吵 —— 一个天天响的告警等于没有告警。
    """
    _, body = _split_frontmatter_body(content)
    hits = [w for w in _CONCLUSION_WORDS if w in body]
    if hits:
        logger.warning(
            "catfish-memory wiki: %s 正文出现禁用结论词 %s —— prompt 要求只 rephrase "
            "日志字面事实, 不做因果推断/价值判断. 内容照写不拦, 但这是蒸馏跑偏的信号.",
            rel_path, "/".join(hits),
        )
    return hits


def _normalize_slug_for_dedup(stem: str) -> str:
    r"""跟 Companion wiki_write.rs:54 normalize_slug 同一套口径。

    去掉空白 / - / _ / . / 全角空格再小写。`zhongdian-fufu` 和 `zhongdianfufu`
    和 `zhong-dian-fu-fu` 规范化后都是 `zhongdianfufu`。
    """
    return "".join(
        c for c in stem
        if not c.isspace() and c not in ("-", "_", ".", "\u3000")
    ).lower()


def _read_title_of(path: Path) -> str:
    """拿这个文件**在 UI 上显示的名字**。

    跟读侧 (wiki_read.rs build_file_info) 同一套 fallback: frontmatter title
    取不到就用文件名。8/4 实测有条目是纯 markdown 完全没 frontmatter
    (信息安全中心.md, 232 字节), 只按 frontmatter title 分组会漏掉它 ——
    而 UI 上它显示的就是"信息安全中心", 跟另一条一模一样。
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return ""
    fm, _ = _split_frontmatter_body(head)
    if fm:
        for line in fm.splitlines():
            line = line.strip()
            if line.startswith("title:"):
                t = line.split("title:", 1)[1].strip()
                if t:
                    return t
    return path.stem          # ← 跟读侧 fallback 一致


def _title_of_content(text: str) -> str:
    """从**还没写盘的内容**里拿 title。

    ⚠ 这个函数存在的原因: _redirect_to_existing_equivalent 拿到的 rel_path 指向
    一个还不存在的文件 (那正是它要判断该不该新建的东西)。第一版拿 Path 去读它,
    永远读到空 → title 判据一次都不会触发, 而且**不报错**。
    又是今天查了一整天的那种形状: 规则写了, 静默不生效。
    """
    fm, _ = _split_frontmatter_body(text[:600])
    if not fm:
        return ""
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("title:"):
            return line.split("title:", 1)[1].strip()
    return ""


def _redirect_to_existing_equivalent(
    catfish_home: Path, rel_path: str, content: str = "",
) -> str:
    r"""同一实体的 slug 变体 → 指回已有的那个文件, 走 merge 而不是新建。

    # 为什么会有变体 (8/4 鸿波 "为什么会被拆成多个")

    实测鸿波机器 255 条里, **21 组同 title 多文件, 共 50 个文件 (20%)**:

        「高新技术企业认定」 × 5   gaoxinjishu-qiye-rending / gaoxinjishu-qiye-ren-ding
                                  / gaoxin-jishu-qiye-rending / gaoxinjishuqiyerending
                                  / gao-xin-ji-shu-qi-ye-ren-ding
        「中电福富」        × 3   zhongdianfufu / zhongdian-fufu / zhong-dian-fu-fu

    三个原因叠在一起:

    1. Generation prompt 的 slug 规则**自相矛盾** (:335):
         "slug = 中文转拼音**首字母** (e.g. 陈鸿波 → chenhongbo, 中电福富 → zdff)"
       说首字母, 举的例子一个是首字母 (zdff) 一个是全拼 (chenhongbo)。同一句话
       两套规则, LLM 只能猜 —— 实测产出的是第三种 (全拼, 分词每次不同)。

    2. _call_generation_llm 只喂 analysis, **LLM 不知道已有哪些条目**, 每次从零造。

    3. _write_wiki_files 只做 `target.exists()` **精确路径匹配** —— 变体路径不同,
       于是每次都建新文件。

    而 Companion UI 建条目那条路 (wiki_write.rs::find_normalized_collision) 有
    规范化重名检测, 13/21 组它本来就能拦住。**又是两条产线只有一条设防** ——
    跟 8/3 mac/Windows 打包那次同一个形状。

    # 这个函数做什么

    写盘前查同目录里有没有规范化等价的文件。有就把写入路径**指回那一个**, 于是
    落进已有的 merge 逻辑 (P18/P19) 而不是新建。

    只认规范化等价 (差连字符/下划线/大小写), **不做模糊匹配** —— "中电福富" 和
    "中电福富信息科技有限公司" 规范化后不同, 不会被误合。那种要靠语义判断, 不是
    这里该干的事, 硬合会把两个真实体揉成一个, 比重复更糟。
    """
    d = catfish_home / rel_path
    if d.exists():
        return rel_path                      # 精确命中, 原逻辑已能处理
    parent = d.parent
    if not parent.is_dir():
        return rel_path
    want = _normalize_slug_for_dedup(d.stem)
    # 判据用**员工看得见的名字**: frontmatter title 取不到就 fallback 文件名,
    # 跟读侧 build_file_info 同一套规则。
    new_title = _title_of_content(content) or d.stem
    if not want and not new_title:
        return rel_path
    try:
        for q in sorted(parent.iterdir()):
            if q.suffix != ".md" or q.name == d.name:
                continue
            # 判据 ①: title 完全相同 —— 比 slug 规范化更强。
            #
            # 8/4 实测: 规范化等价只覆盖 13/21 组, 剩下 8 组是"中文名 vs 拼音名"
            # (高新资质申报 vs gaoxin-zizhi-shenbao / 陈秀平 vs chenxiuping) 或
            # 拼音转写不同 (zizhi-duibiao-fenxi-yuanze vs zizhibiaofenxifenze),
            # slug 规范化后仍然不等 —— 但 title 一模一样。
            #
            # title 是员工在 UI 上**唯一看得见的东西**。两条 title 完全相同的
            # 条目, 员工根本分不出是两个东西 —— 那就该是一个。
            if new_title and new_title == _read_title_of(q):
                new_rel = str(Path(rel_path).parent / q.name)
                logger.info(
                    "catfish-memory wiki 去重: %s 跟已有的 %s **title 相同**, "
                    "改成更新那一个",
                    rel_path, new_rel,
                )
                return new_rel
            # 判据 ②: slug 规范化等价 (差连字符/下划线/大小写)
            if _normalize_slug_for_dedup(q.stem) == want:
                new_rel = str(Path(rel_path).parent / q.name)
                logger.info(
                    "catfish-memory wiki 去重: %s 跟已有的 %s 规范化等价, "
                    "改成更新那一个 (LLM 每次造的拼音变体不同, 见函数注释)",
                    rel_path, new_rel,
                )
                return new_rel
    except OSError:
        pass
    return rel_path


# ─────────────────────────────────────────────────────────────
# 受控词表 —— 把"事实上已经存在的本体"显式化 (8/4 鸿波 "是不是应该用 ontology")
# ─────────────────────────────────────────────────────────────
#
# 8/4 实测鸿波机器的类型分布:
#
#     entity_type   cert 81 · person 13 · project 8 · org 6 · department 3
#                   + notification/standard/data 各 1 · 空 33 (22%)
#     concept_type  principle 18 · standard 18 · process 17 · rule 15
#                   + 规则 1 · 标准 1 · 流程 1 · system 1 · 空 1
#
# 两个事实同时成立:
#   · 类型**自然收敛**了 —— entity 前 5 类覆盖 111/147, concept 前 4 类覆盖 68/73。
#     不是无限发散, 定词表的成本很低。
#   · 但没有约束, 边缘就漂 —— rule/规则、standard/标准、process/流程 中英文并存,
#     指的是同一个东西。
#
# P3.5.176 删掉 enum 的理由是"enum 是硬编码"。但结果不是"更灵活", 是**没有词汇表**:
# 同义词各写各的, 筛选和分组就散了。
#
# 所以这里不是引入一个新本体, 是把已经长出来的那个写下来 + 加校验。
# 设计上守两条:
#   1. **归一化而不是拒绝** —— 规则→rule 这种直接改, 不丢数据
#   2. **不认识的新类型放行 + WARNING** —— 词表要能长, 但长了要有人知道
#
_TYPE_ALIASES = {
    # concept
    "规则": "rule", "标准": "standard", "流程": "process", "原则": "principle",
    "体系": "system", "方法": "method", "规范": "standard",
    # entity
    "证书": "cert", "资质": "cert", "认证": "cert",
    "人": "person", "人员": "person", "员工": "person",
    "公司": "org", "机构": "org", "组织": "org",
    "部门": "department", "项目": "project", "文件": "doc", "文档": "doc",
    "数据": "data", "通知": "notification",
}

_ENTITY_TYPES = frozenset({
    "cert", "person", "org", "department", "project",
    "doc", "data", "system", "notification", "standard",
})
_CONCEPT_TYPES = frozenset({
    "principle", "standard", "process", "rule", "system", "method",
})


def _canon_subtype(rel_path: str, raw: str) -> tuple[str, bool]:
    """把 entity_type / concept_type 归一化到受控词表。

    返 (归一化后的值, 是否表外)。表外不拒绝, 只报告 —— 词表要能长。
    """
    v = (raw or "").strip()
    if not v:
        return "", False
    low = v.lower()
    canon = _TYPE_ALIASES.get(v) or _TYPE_ALIASES.get(low) or low
    known = _ENTITY_TYPES if "entities/" in rel_path else _CONCEPT_TYPES
    if canon != low:
        logger.info(
            "catfish-memory wiki: %s 的类型 %r 归一化成 %r (受控词表)",
            rel_path, v, canon,
        )
    elif canon not in known:
        logger.warning(
            "catfish-memory wiki: %s 用了词表外的类型 %r —— 不拦, 但词表该不该加它, "
            "值得看一眼 (现有: %s)",
            rel_path, canon, "/".join(sorted(known)),
        )
        return canon, True
    return canon, False


_SUBTYPE_LINE = re.compile(r"^(entity_type|concept_type):\s*(.*)$", re.MULTILINE)


def _normalize_types(rel_path: str, content: str) -> str:
    """写盘前把 frontmatter 里的类型字段归一化。"""
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    changed = False

    def _sub(m: "re.Match[str]") -> str:
        nonlocal changed
        key, val = m.group(1), m.group(2).strip()
        canon, _ = _canon_subtype(rel_path, val)
        if canon and canon != val:
            changed = True
            return f"{key}: {canon}"
        return m.group(0)

    new_fm = _SUBTYPE_LINE.sub(_sub, fm)
    if not changed:
        return content
    return f"---\n{new_fm}\n---\n{body}"


# ─────────────────────────────────────────────────────────────
# 引用完整性 —— related 指向不存在的节点 (8/4 实测 65/408 = 16%)
# ─────────────────────────────────────────────────────────────


def _check_dangling_related(catfish_home: Path, rel_path: str, content: str) -> list[str]:
    r"""报告 related 里指向不存在节点的名字。只报告, 不改内容。

    8/4 实测: 408 条 related 边里 65 条 (16%) 指向的节点根本不存在。

    为什么不自动删: dangling 有两种, 语义完全相反 ——
      · LLM 编了个不存在的东西        → 该删
      · 这个节点还没被蒸馏出来, 之后会有 → 删了反而破坏未来的连接
    分不清就不该动。而且 WikiTree 有 dangling 点击自动建真文件的路径 (P3.5.114),
    说明产品上是把它当"待补"而不是"错误"看的。

    但 16% 无声无息不行 —— 图谱上那些边直接消失, 没人知道。报出来。
    """
    fm, _ = _split_frontmatter_body(content)
    if not fm:
        return []
    names = [
        n.strip().strip('"').strip("'").strip("[]").strip()
        for n in _parse_frontmatter_lists(fm).get("related", [])
    ]
    names = [n for n in names if n]
    if not names:
        return []
    # 8/4: 改走 wiki_resolve —— 前端 lib/wikiResolve.ts 同一套规则, 由
    # edge/contracts/wiki_resolve_cases.json 对拍钉住。
    #
    # 原来这里是"stem/title 精确相等", 比前端严格得多 (前端还有别名、标点归一、
    # 唯一子串三档)。于是同一条边蒸馏侧报断链、图上其实连着 —— 报出来的 16%
    # 里大部分是假警报, 真问题反而被淹掉。
    from wiki_resolve import load_nodes, resolve_wiki_ref

    nodes = load_nodes(catfish_home)
    missing = [n for n in names if resolve_wiki_ref(n, nodes).kind != "hit"]
    if missing:
        logger.info(
            "catfish-memory wiki: %s 的 related 有 %d 个指向不存在的节点 (%s) —— "
            "可能是 LLM 编的, 也可能是还没蒸馏出来的, 不自动删",
            rel_path, len(missing), ", ".join(missing[:4]),
        )
    return missing


# ─────────────────────────────────────────────────────────────
# 人工修正保护 (8/4 鸿波 "有的数据还是需要人修正的")
# ─────────────────────────────────────────────────────────────

_AUTHORED_BY_EMPLOYEE = re.compile(r"^authored_by:\s*employee\s*$", re.MULTILINE)
_LLM_APPENDIX_HEAD = "## 蒸馏补充 (待你确认)"


def _is_employee_authored(text: str) -> bool:
    """这条是不是员工亲手写/改过的。"""
    fm, _ = _split_frontmatter_body(text)
    return bool(fm) and bool(_AUTHORED_BY_EMPLOYEE.search(fm))


def _append_as_appendix(old_text: str, new_text: str) -> str:
    r"""员工改过的条目 —— 新蒸馏内容只能进附录, 不许覆盖正文。

    # 为什么 (8/4)

    实测: 220 条 wiki 里 **219 条是 LLM 生成的, 只有 1 条 sources=manual**。
    而 frontmatter 里**没有任何字段记录"这条是谁写的"** —— 没有 author, 没有
    reviewed, 没有 verified。

    后果不是"信息缺失"。merge_files_with_llm 读文件时根本不看来源:

        old_text = target.read_text(...)          # 不管这是谁写的
        merged = await _call_merge_llm(old_text, new_content, model)

    **员工在知识体系 TAB 里手工改的内容, 下一次蒸馏会被原样喂给 LLM 重写。**
    你改掉一条错的断言, 几天后它可能又变回去了, 而且不会收到任何提示。

    8/4 加的那些验收只检查"字段有没有丢、正文有没有暴缩", **不检查"这段是不是
    人写的"** —— 一次完全合规的 LLM 重写照样能把人的修正抹掉。

    # 做法

    员工改过的条目 (authored_by: employee):
      · **正文原样保留**, 一个字不动
      · frontmatter 的 list 字段仍取并集 (tags/related 是累加语义, 不冲突)
      · 新蒸馏内容进「蒸馏补充 (待你确认)」附录, 员工自己决定要不要并进去
      · 多次蒸馏只保留最近一份附录, 不层叠

    不是直接丢掉新内容 —— 那会让知识库停止更新。是把**裁决权交回给人**:
    机器可以提出, 但改不了人已经定下的东西。
    """
    old_fm, old_body = _split_frontmatter_body(old_text)
    new_fm, new_body = _split_frontmatter_body(new_text)

    merged_fm = old_fm
    for field in _FM_LIST_FIELDS_UNION:
        old_lists = _parse_frontmatter_lists(old_fm)
        new_lists = _parse_frontmatter_lists(new_fm)
        union: list[str] = []
        seen: dict[str, int] = {}
        for v in old_lists.get(field, []) + new_lists.get(field, []):
            n = _rel_item_name(v) if field == "related" else v
            if n in seen:
                if v.startswith("{"):
                    union[seen[n]] = v
                continue
            seen[n] = len(union)
            union.append(v)
        if not union:
            continue
        quoted = ", ".join(
            v if (v.startswith("{") or v.startswith('"')) else f'"{v}"' for v in union
        )
        line = f"{field}: [{quoted}]"
        if re.search(rf"^{field}:\s*\[.*?\]\s*$", merged_fm, re.MULTILINE):
            merged_fm = re.sub(
                rf"^{field}:\s*\[.*?\]\s*$", line, merged_fm, count=1, flags=re.MULTILINE
            )
        else:
            merged_fm = merged_fm.rstrip() + "\n" + line

    # 正文: 员工那份原样保留, 砍掉上一次的附录再贴新的 (不层叠)
    body = re.split(rf"^{re.escape(_LLM_APPENDIX_HEAD)}\s*$", old_body, maxsplit=1,
                    flags=re.MULTILINE)[0].rstrip()
    add = new_body.strip()
    if add:
        body += (
            f"\n\n{_LLM_APPENDIX_HEAD}\n\n"
            "<!-- 这段是后台蒸馏新抽出来的, 没有覆盖你写的正文。确认后可以自己并进去, "
            "或者直接删掉这一节。 -->\n\n" + add + "\n"
        )
    return f"---\n{merged_fm}\n---\n\n{body}\n"


def _write_wiki_files(
    catfish_home: Path,
    files: Dict[str, str],
    skip_merge_paths: Optional[set] = None,
) -> Tuple[int, int]:
    """写 wiki files 真 ~/.catfish/wiki/entities/ + wiki/concepts/. 返 (n_entities, n_concepts).

    P18 (6/5 鸿波): 同 slug 触发 _merge_wiki_file (frontmatter list 并集 +
    保留 created + body 新+ 旧 legacy 注释留底), 不再无脑 overwrite.
    P19 (6/5 鸿波): skip_merge_paths 真 path set 已被 _call_merge_llm 处理过
    (LLM merge), 直接 overwrite. 没 merge `走 P18 regex merge 安全网`.
    新建 file (不重名) 沿用 overwrite.
    """
    if not files:
        return (0, 0)
    skip = skip_merge_paths or set()
    n_entities = 0
    n_concepts = 0
    for rel_path, content in files.items():
        # 8/4: LLM 每次造的拼音 slug 不一样 → 先看有没有规范化等价的已有文件,
        # 有就指回去走 merge, 不新建。
        rel_path = _redirect_to_existing_equivalent(catfish_home, rel_path, content)
        target = catfish_home / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            final_content = _ensure_frontmatter_fence(rel_path, content)
            if target.exists() and _is_employee_authored(
                target.read_text(encoding="utf-8", errors="replace")
            ):
                # 8/4: 员工改过的条目 —— LLM 不许覆盖正文, 新内容只进附录。
                try:
                    old_text = target.read_text(encoding="utf-8")
                    final_content = _append_as_appendix(old_text, content)
                    logger.info(
                        "catfish-memory wiki: %s 是员工改过的 (authored_by: employee), "
                        "正文保持原样, 新蒸馏内容放进「蒸馏补充」附录等他确认",
                        rel_path,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory 附录合并 %s 失败, **保留员工原文不动**: %s",
                        rel_path, e,
                    )
                    final_content = target.read_text(encoding="utf-8", errors="replace")
            elif target.exists() and rel_path not in skip:
                # 重名 + LLM 没处理 → P18 regex merge 安全网
                try:
                    old_text = target.read_text(encoding="utf-8")
                    final_content = _ensure_frontmatter_fence(
                        rel_path, _merge_wiki_file(old_text, content)
                    )
                    logger.info(
                        "catfish-memory wiki regex merge (P18 fallback): %s",
                        rel_path,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "catfish-memory wiki merge %s 失败 (fallback overwrite): %s",
                        rel_path, e,
                    )
                    final_content = _ensure_frontmatter_fence(rel_path, content)
            final_content = _normalize_types(rel_path, final_content)
            _scan_conclusion_words(rel_path, final_content)
            _check_dangling_related(catfish_home, rel_path, final_content)
            target.write_text(final_content + ("\n" if not final_content.endswith("\n") else ""), encoding="utf-8")
            if "entities/" in rel_path:
                n_entities += 1
            elif "concepts/" in rel_path:
                n_concepts += 1
        except OSError as e:
            logger.warning("catfish-memory write wiki file %s 失败: %s", rel_path, e)
    return (n_entities, n_concepts)


