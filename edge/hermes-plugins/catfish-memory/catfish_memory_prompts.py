"""四段 LLM 提示词模板 —— 从 catfish_memory_helpers.py 拆出 (8/15)。

纯常量, **零依赖**: 不 import 任何本地模块, 也不被本地模块以外的东西依赖。
放在最前面, 是这一组文件里唯一的叶子。

改这里要当心: `_GENERATION_PROMPT_TEMPLATE` 曾因为从 10421 涨到 13099 字节
把 advisor 全打到 300s 超时 (briefing_advisor.ts:511 记着那次)。提示词长度
是有代价的, 不是白加。
"""
from __future__ import annotations

import time


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
    "- <name> | <type: person/org/department/project/cert/doc> | <一句话, ≤50字>\n"
    "- ... (≤6 条 entities, 按重要性排, 企业业务相关)\n\n"
    # 9/24 鸿波"知识库很多数据有问题": 这里原来写"**必须至少 3 个** concepts" + 一串示例,
    # 等于逼 LLM 每批日志造 3 条规则 —— 实测一段 44 条的微信群聊被造出「材料版本控制流程」
    # 「终验报告归档规则」等 5 条, 全是把一次沟通拔高成"规则"。改成可以一条都没有。
    "## Concepts\n"
    "- <name> | <type: process/rule/principle/standard/scope> | <一句话, ≤50字>\n"
    "- ... (0-4 条. **只抽员工明确定下或确认的**口径/规则/流程 —— 原文要有\"以后都…\"\"统一…\"\"口径是…\"这类表态. "
    "一次沟通、一次处理、别人的一句话都不是规则; 没有就写 `(无)`, 不要凑)\n\n"
    "## Decisions\n"
    "- <YYYY-MM-DD> | <who> | <decided what> | <why>\n"
    "- ... (≤5 条, 最近)\n\n"
    "## Contradictions\n"
    "- <pair A vs B>: <冲突点, ≤80字>\n"
    "- ... (≤3 条, 没有就写 `(无)`)\n\n"
    "**约束**:\n"
    "- entity name 简短 (人名/机构缩写/产品名/资质名), 拼写跟员工原文一致\n"
    # 9/24: 实测把微信昵称「杨❤宇」当真名建了「杨宇」, 又把采购部职员写成"上级领导"
    "- 人名用真名: 聊天记录里昵称和\"单位-姓名\"(如 @金智达-杨新宇) 并存时以后者为准; "
    "原文没说清的身份关系 (上级/下属/外部单位) 不要写, 不要猜\n"
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
    "created: __TODAY__\n"
    "updated: __TODAY__\n"
    "tags: [<tag1>, <tag2>]\n"
    # 8/4: 加上 typed 形式。读侧 6/29 (P3.5.132 #5) 就支持 {name, rel} 了, 但
    # **prompt 从头到尾没提过 rel** —— 实测 408 条边 0 条带类型, 不是 LLM 不配合,
    # 是根本没人要求过它。功能建在读侧、写侧不知道, 等于没建。
    "related: [{name: \"<名字>\", rel: \"<下面 15 个关系词之一>\"}, {name: \"<名字>\", rel: \"关联\"}]\n"
    # P3.5.205 (7/9 鸿波 catch): sources 从常量 `[employee_journal]` 改**日志日期列表**,
    # 让员工能反查每 wiki 页来自哪几天日志. 格式: `[journal:YYYY-MM-DD, ...]` (取自
    # Analysis Decisions 段的 YYYY-MM-DD 字段, 或员工日志里 heading `## [ts] journal`
    # 的日期部分). 只列涉及该 entity/concept 的日期, 去重升序. 老 wiki 遇 `[employee_journal]`
    # 单值 → merge 时 LLM 按新格式升级.
    "sources: [\"journal:<YYYY-MM-DD>\", ...]\n"
    "---\n\n"
    "# <name>\n\n"
    "<正文 ≤600 字: 规则/口径原文 + 谁定的、哪天定的 + 适用范围. 有多少事实写多少, 不凑段落, 不写日志里没有的通用介绍>\n"
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
    "- 禁推测句: 这表明 / 可能 / 体现了 / 有助于 / 需要与…沟通 —— 原文没写的不写 (9/24)\n"
    "- 进度/状态句带日期: \"截至 YYYY-MM-DD, …\", 不写不带日期的\"目前\"\n"
    "\n"
    "---FILE: wiki/entities/<slug>.md---\n"
    "---\n"
    "type: entity\n"
    "title: <name>\n"
    # P3.5.176: entity_type 严格删 enum. LLM 自主填**贴切语义短词**. 员工场景
    # diverse (e.g. 'org' / 'person' / 'project' / '部门' / '证书' 等), 不 enum.
    "entity_type: <一个贴切语义的短词, 员工可读>\n"
    "created: __TODAY__\n"
    "updated: __TODAY__\n"
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
    "related: [{name: \"<名字>\", rel: \"<下面 15 个关系词之一>\"}, {name: \"<名字>\", rel: \"关联\"}]\n"
    # P3.5.205 (7/9 鸿波 catch): sources 从常量 `[employee_journal]` 改**日志日期列表**,
    # 让员工能反查每 wiki 页来自哪几天日志. 格式: `[journal:YYYY-MM-DD, ...]` (取自
    # Analysis Decisions 段的 YYYY-MM-DD 字段, 或员工日志里 heading `## [ts] journal`
    # 的日期部分). 只列涉及该 entity/concept 的日期, 去重升序. 老 wiki 遇 `[employee_journal]`
    # 单值 → merge 时 LLM 按新格式升级.
    "sources: [\"journal:<YYYY-MM-DD>\", ...]\n"
    "---\n\n"
    "# <name>\n\n"
    "<正文 ≤400 字: 是什么 + 日志里的具体事实 (带日期). 有多少写多少, 一句话也可以, 不凑段落>\n"
    "\n"
    # P3.5.205 (7/9 鸿波): 同 concept body 约束, entity body 也走.
    "**body 写作约束 (P3.5.205)**:\n"
    "- 只用日志字面出现的事实 rephrase, 不做因果推断 / 价值判断 / 生动化修饰\n"
    "- 允许衔接词: 同时 / 然后 / 目前 / 另外\n"
    "- 禁结论词: 决定 / 因此 / 意味着 / 影响 / 视为红线 / 直接影响\n"
    "- 禁推测句: 这表明 / 可能 / 体现了 / 有助于 / 需要与…沟通 —— 原文没写的不写 (9/24)\n"
    "- 进度/状态句带日期: \"截至 YYYY-MM-DD, …\", 不写不带日期的\"目前\"\n"
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
    # 9/17: rel 改成**闭合词表** (contracts/wiki_relation_vocab.json)。之前写的是
    # "2-4 字短词 + 几个例子", LLM 只能自造 (持有主体/采用口径/同期项目/不同条目),
    # 写入侧现在会把表外值归成「关联」→ pending。词表直接印在 prompt 里, 让它
    # 有得选; 方向也说清 —— rel 是 <本条目> → <对方>。
    "- `related:` **必须带关系类型**: `{name: \"中电福富\", rel: \"隶属\"}`. "
    "rel **只能从这 15 个里选** (方向: 本条目 → 对方): "
    "隶属 (本条目属于对方) / 包含 (本条目包含对方) / 负责 / 参与 / 协作 / "
    "依据 (本条目以对方为依据) / 遵循 / 使用 / 持有 (公司→证书) / 认证 (公司→评估机构或标准) / "
    "对标 / 配套 / 替代 (新→旧) / 前置 (本条目是对方的前置条件) / 同类. "
    "15 个都不贴切才写 `rel: \"关联\"` —— 它会进待确认队列由员工定, "
    "**不要自造别的词** (写了也会被归成「关联」).\n"
    "- `related:` 必须真双引号 string list — 正确: "
    "`related: [\"[[陈鸿波]]\", \"[[FFCS]]\"]`. "
    "**错**: `related: [[[陈鸿波]]]` (3 个 `[` YAML 真 inline list of list, "
    "Obsidian 不能 parse)\n"
    # 9/17: sources 也说死。51 篇还挂着 `employee_journal`, 等于没写来源;
    # 写入侧现在只收下面三种, 日期还会对照本轮真读过的日志, 编的直接丢。
    "- `sources:` 只允许三种写法, 每一篇至少一条: "
    "`\"journal:YYYY-MM-DD\"` (日期必须是输入日志里 `## [YYYY-MM-DD ...]` 抬头真出现过的, "
    "只列跟本条目有关的那几天) / "
    "`\"raw/sources/<文件名不含.md>\"` (来自 `### source: <文件名>` 段) / `manual`. "
    "**禁止** `employee_journal` 这种泛称, 禁止编日期.\n"
    "- 每 file title 不重复\n"
    "- related wikilinks 真 `[[name]]` 必须指真 Analysis 里出现真 name\n"
    "- 同 slug 真 entity vs concept 真不允许 (按 type 分)\n"
    "- 全部输出 ≤6000 字\n"
)


#: 生成 prompt 里唯一要替换的东西。**不用 `{}` 形式的占位符** —— 理由见下。
_TODAY_PLACEHOLDER = "__TODAY__"


def _build_generation_prompt() -> str:
    """把 __TODAY__ 换成今天的日期, 生成 Step 2 的 prompt。

    # ⚠ 为什么不用 str.format —— 它让 wiki 生成整天零产出 (8/15 晚查出来)

    原来这里是:

        return _GENERATION_PROMPT_TEMPLATE.format(today=time.strftime("%Y-%m-%d"))

    而模板里有三处**写给 LLM 看的字面量花括号** (frontmatter 示例):

        {name: "<名字>", rel: "<下面 15 个关系词之一>"} ×2 (9/17 起每行还多一个「关联」示例)
        {name: "中电福富", rel: "隶属"}              ×1

    `.format()` 见到 `{name: ...}` 就去找名叫 `name` 的参数, 抛 KeyError('name')。

    ## 后果链条 —— 全程没有一个红色信号

        KeyError → _call_generation_llm 的 except Exception 吞成 warning
                 → 返 None
                 → 日志打 "wiki Step 2 generation 返空 (skip)"

    最后那句 INFO 读起来像"这次没什么可生成的", 实际是"每次都失败"。
    8/15 当天 agent.log 里 17 次, 从 09:30 到 18:16 —— 也就是 wiki 条目生成
    这个功能**一整天零产出**, 而没有任何人会注意到。

    ## 为什么改成 replace 而不是把花括号转义成 {{}}

    转义能修好这一次, 但留了个雷: 提示词是要经常改的, 下一个往里加 JSON /
    frontmatter 示例的人不会知道这里跑过 `.format`, 加完照样炸, 而且照样
    静默。用一个内容里不可能出现的哨兵串, 整类问题就没了。

    tests/test_generation_prompt.py 钉住这件事。
    """
    return _GENERATION_PROMPT_TEMPLATE.replace(
        _TODAY_PLACEHOLDER, time.strftime("%Y-%m-%d")
    )


#: 9/24: 蒸馏收尾的"当前状态"合并。背景见 catfish_memory_distill_reconcile.py ——
#: 分段蒸馏时每段只看得到自己那 8000 字, 6 月那段写"CMMI-5 评审进行中"是对的,
#: 它不可能知道 8/11 已经拿证。只有把所有段放在一起、以最新为准, 才分得清。
_RECONCILE_PROMPT = (
    "下面是员工工作日志的蒸馏片段, **按时间从新到旧**排列, 每段标了日期范围。\n"
    "同一件事在不同时间状态不同: **以最新提到它的那段为准**。旧段写'进行中/准备中/评审中', "
    "如果更新的段写了完成/拿证/获证/出证/结项/关闭/撤销, 这件事就是已完结。\n\n"
    "输出三段 markdown, 不要别的:\n"
    "## 进行中\n- <事项>：<最新状态>（最后提及 YYYY-MM-DD）\n(≤12 条, 只列最近一个月还在推进的)\n\n"
    "## 已完结（不再进待办）\n- <事项>：<完结依据, 如证书编号/结项日期>（YYYY-MM-DD）\n(≤15 条)\n\n"
    "## 暂停/搁置\n- <事项>：<原因>（YYYY-MM-DD）\n(≤6 条, 没有写 (无))\n\n"
    "约束: 每条一行 ≤60 字; 只用输入里的事实, 不推测; 同一件事只出现在一段里; "
    "超过一个月没再提、也没写完结的事项不要放进「进行中」。\n"
)
