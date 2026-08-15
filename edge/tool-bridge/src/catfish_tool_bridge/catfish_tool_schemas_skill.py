"""skill 的安装 / 提议 / 冻结 / 教学 / 发布 —— 14 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。
"""
from __future__ import annotations

from typing import Any, Dict, List


SKILL_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "catfish_skill_backup",
        "description": (
            "更新 / 删除一个 skill 之前**必须**调这个 tool 做 backup. "
            "把当前 SKILL.md 复制到 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md. "
            "员工说 '回退 X skill' 时, 模型可以从 .versions/ 拿最近一版替换. \n\n"
            "✅ 调用时机:\n"
            "  - skill_manage(action=update) 之前\n"
            "  - skill_manage(action=delete) 之前 (即使要删, 也留 .versions/ 历史)\n\n"
            "❌ 不该调的场景:\n"
            "  - skill_manage(action=create) (新建无老版可备)\n"
            "  - 员工跟你聊天没明确要改 skill\n\n"
            "调用后会返回 {ok, backup_path, version_count} 让你确认 backup 真做了, "
            "然后再调 skill_manage update/delete 才合规. "
            "不调直接 update 会被 catfish-policy R10 deny."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "skill 全名, 例如 'productivity/catfish-email' 或 "
                        "'productivity/expense-submit'. 用 / 分隔 namespace 和 skill 名."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "为啥要改/删这个 skill — 一句话, 员工会看到, 也写日志",
                },
            },
            "required": ["skill_name", "reason"],
        },
        "emoji": "💾",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_run_skill",
        "description": (
            "调用 catfish 工程审定 skill (含**凝固后的浏览器自动化 skill** 如 "
            "eis-login + **渲染类 skill** 如 weekly-report). 优先于自己写代码 / "
            "自己 step-by-step 调 catfish_browser_* — gateway 在 system prompt "
            "已经把可用 skill 列表注入给你, 看到列表里有匹配的**立即**调.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说'登 EIS' / '上 EIS 看待办' → catfish_run_skill("
            "skill_path='department/eis-login', params={'username': 'chenhb'})\n"
            "  - 员工说'写给领导的请示件' → catfish_run_skill(skill_path="
            "'department/leadership-briefing', params={...})\n"
            "  - 任何 skill 列表覆盖的场景\n\n"
            "❌ 不该调用:\n"
            "  - skill 列表里没有的能力 → 走 execute_code 临时写, **不要** 用 "
            "catfish_browser_* 手工干 skill 该干的事\n\n"
            "**第一次不知道参数?** params={'_help': True} 调一次拿 schema.\n\n"
            "**★★★ skill 失败时的铁律 (BL-MM9-FREEZE-v2 5/12)** ★★★:\n"
            "  如果本 tool 返 ok=false (例 EIS skill goto 冷启动失败), **绝对不要**\n"
            "  自己调 catfish_browser_goto / fill / click 等手工接管 — skill 里的 "
            "selector 是教学时验证过的, 你手工推的 selector 不可靠, 会污染 chrome "
            "状态 + 走偏. **必须**:\n"
            "    1. 把失败原因清楚告诉员工 (skill 名 + error 字段)\n"
            "    2. 问员工: '要不要再试一次 / 重教这个 skill / 我手工接管?'\n"
            "    3. 员工 explicit 说手工 → 才允许调 catfish_browser_*\n"
            "  这是 catfish 凝固 skill 的核心承诺 — skill 失败 ≠ 你接手, skill 失败 "
            "= 报告员工.\n\n"
            "**返回**: {ok, files: [paths], summary, error}. files 自动渲染成 pill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "skill 相对路径, 来自 system prompt 注入的 skill 列表. "
                        "例: 'department/leadership-briefing'. 不带前导 / 后导 /."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "skill render 函数的入参. 不知道传什么时, 用 "
                        "{'_help': True} 调一次拿 schema."
                    ),
                },
            },
            "required": ["skill_path", "params"],
        },
        "emoji": "📑",
        "toolset": "catfish_native",
        # 4-30 一度试 B 方案 (hermes 原生) 失败, 立刻撤回. catfish_run_skill 是
        # 模型唯一靠谱的 catfish skill 调用入口, 必须 available=True.
        "available": True,
    },
    {
        "name": "catfish_skill_install",
        "description": (
            "本机安装一个 skill — 两种来源二选一:\n"
            "  (A) 本机目录 source_dir (例 ~/Downloads/x-skill/) — Day 3 MVP, 同事拿目录给员工的场景\n"
            "  (B) 中央 Skills Hub hub_skill (例 'shared/feishu-expense@latest') — Phase 2 (5/5 ship)\n\n"
            "✅ 调用场景:\n"
            "  - 员工说 '装 hub 里的 X skill' / '从 hub 拿 Y' → 用 hub_skill\n"
            "  - 员工说 '把这个 skill 装上' (给本地目录) → 用 source_dir\n\n"
            "❌ 不该调用:\n"
            "  - 员工没明确要求安装\n"
            "  - source_dir 在系统目录 (/etc, /usr 等) — 安全考虑拒绝\n\n"
            "**Hub 模式格式**:\n"
            "  hub_skill: 'namespace/name@version', 例 'shared/feishu-expense@1.0.0'.\n"
            "  version 写 'latest' 拿最新版.\n"
            "  hub_url: 默认 env CATFISH_HUB_URL 或 http://127.0.0.1:9001.\n\n"
            "**安装规则**:\n"
            "  1. SKILL.md 必须 (script.py 可选)\n"
            "  2. SKILL.md frontmatter 的 name 字段 → 决定安装路径 <namespace>/<name>/\n"
            "  3. 同名已存在 → 必须 overwrite=true 才覆盖\n"
            "  4. 安装后自动 dry-run 验证 + audit log + 仪表盘出现\n\n"
            "**返回**: {ok, installed_path, error, source: 'local'|'hub'}.\n"
            "**audit**: ~/.catfish/skill_audit.jsonl event_type=install."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_dir": {
                    "type": "string",
                    "description": (
                        "(模式 A) 本机源目录绝对路径或 ~ 开头. 必须含 SKILL.md. "
                        "例: '~/Downloads/my-new-skill/' 或 '/tmp/shared-skill/'. "
                        "跟 hub_skill 互斥, 二选一."
                    ),
                },
                "hub_skill": {
                    "type": "string",
                    "description": (
                        "(模式 B) Skills Hub 中央路径, 'namespace/name@version' 格式. "
                        "例 'shared/feishu-expense@latest' 或 'productivity/eis-export@1.0.0'. "
                        "跟 source_dir 互斥, 二选一."
                    ),
                },
                "hub_url": {
                    "type": "string",
                    "description": (
                        "(模式 B 用) Skills Hub server base URL. "
                        "默认 env CATFISH_HUB_URL, 没设默认 http://127.0.0.1:9001."
                    ),
                },
                "namespace": {
                    "type": "string",
                    "description": (
                        "安装到本机的 namespace, 例 'department' / 'personal' / 'shared'. "
                        "默认 'personal' (员工本人装的). "
                        "Hub 模式不写时, 默认走 hub_skill 自带的 namespace."
                    ),
                    "default": "personal",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "同名 skill 已存在时是否覆盖. 默认 false. "
                        "覆盖前自动 backup 到 skill-trash."
                    ),
                    "default": False,
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_skill_delete",
        "description": (
            "删除一个 catfish 工程审定 skill (整个目录). 五一 sprint Day 2 加.\n\n"
            "✅ 调用场景:\n"
            "  - 员工明确说 '删掉 X skill' / '不再需要 X skill'\n"
            "  - skill 已经废弃 (catfish_run_skill 返回过 deprecated_warning)\n\n"
            "❌ 不该调用:\n"
            "  - 员工只说 '看不到这个 skill 了' (那是其他问题, 不是要删)\n"
            "  - 员工没明确要求删 — 这是不可逆操作, 必须显式确认\n\n"
            "**安全保障**: 删之前自动 backup 到 ~/.catfish/skill-trash/<unix-ts>/, "
            "30 天内可恢复. 真要永久删, 员工 30 天后手动清空 trash.\n\n"
            "**返回**: {ok, deleted_path, backup_path, error}.\n"
            "**audit**: 调用记 ~/.catfish/skill_audit.jsonl event_type=delete, "
            "客户 IT 可审 skill 生命周期.\n\n"
            "**注意**: 删 skill 后, 现有 session 已加载的 module 仍可调 (sys.modules), "
            "但新 session 看不到, 仪表盘自动消失. 想立即生效请重启 Companion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "skill 相对路径, 例 'department/leadership-briefing'. "
                        "跟 catfish_run_skill 用的 skill_path 一致."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "删除原因 — 员工说的话或你判断的, 写 audit log",
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "**必填 true**. 防误删 — 员工没明确说删, "
                        "你不应该自己判断 confirm=true."
                    ),
                },
            },
            "required": ["skill_path", "reason", "confirm"],
        },
        "emoji": "🗑",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM9 (5/8) — agent 自动抽 skill ──
    {
        "name": "catfish_propose_skill",
        "description": (
            "把员工反复做的工作流程**提案**成 skill, 等员工确认再装. **不直接装**.\n\n"
            "✅ 调用场景 (3+ 次同 pattern 必调, 跟 BL-MM7 三 evidence 门槛同哲学):\n"
            "  - 员工本周已经第 3 次让你写 '项目立项材料' 用类似结构 → propose 'project-proposal'\n"
            "  - 员工反复粘贴差旅报销单让你算金额 → propose 'travel-expense-calc'\n"
            "  - 员工每周一让你查 audit log 拼周报 → propose 'weekly-report-from-audit'\n\n"
            "❌ 不该调用 (跟 hermes 黑盒自决 区别):\n"
            "  - 员工只做过 1-2 次 → 还不到 pattern, 静默观察\n"
            "  - 红线场景: 健康 / 财务 / 感情 / 政治 / 宗教 — 永远不 propose 这类 skill\n"
            "  - 员工已经 reject 过同类 propose — 别骚扰\n\n"
            "**调用后**: 写 ~/.catfish/skill_proposals.jsonl, append 一条 (员工可看). "
            "**返回给 LLM 的话术**: '已记下提案, 我现在跟员工说: \"我注意到这周你 X 次 Y, "
            "要不我把流程存成 skill 下次直接调? 你说装我就装.\"' 等员工说 yes 再调 catfish_skill_install.\n\n"
            "**audit**: ~/.catfish/skill_proposals.jsonl event_type=propose, accepted/rejected 由后续事件追加.\n\n"
            "**返回**: {ok, proposal_id, total_proposals, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "拟用作 skill name (kebab-case, 简短描述性). "
                        "例: 'project-proposal' / 'travel-expense-calc' / 'weekly-report-from-audit'."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "为什么觉得该提案 — 1-2 句, 含**具体观察证据** "
                        "(例: '本周 5/5/5/6/5/8 三次让我写项目立项材料, 结构相似 (背景/目标/团队/预算/里程碑)'). "
                        "员工看了能直接确认或反驳."
                    ),
                },
                "action_steps": {
                    "type": "string",
                    "description": (
                        "skill 大致做啥的 3-5 步 markdown bullets. "
                        "例: '1. 读员工提供的项目背景\\n2. 拉历史立项材料样本\\n"
                        "3. 按公司模板拼 6 段 (背景/目标/团队/预算/里程碑/风险)\\n"
                        # 8/14: 跟全局约定对齐 (~/.catfish/outputs/<YYYY-MM-DD>/)。
                        # 老例子写的是 output/ 平铺 + <ts> 前缀, 是第三种写法。
                        "4. 输出到 ~/.catfish/outputs/<YYYY-MM-DD>/立项-<项目>.docx'. "
                        "员工 accept 后 LLM 用这个 outline 调 catfish_skill_install."
                    ),
                },
                "evidence_count": {
                    "type": "integer",
                    "description": (
                        "你观察到员工做这事的次数. 两套阈值 (跟 triggered_by 配套):\n"
                        "  - triggered_by='auto' (你自己观察 propose): **必须 ≥3**, "
                        "不到 3 次不算 pattern, 静默观察.\n"
                        "  - triggered_by='user_request' (员工显式说 '存成 skill'): **≥1 即可**, "
                        "员工说做就做不卡阈值."
                    ),
                    "minimum": 1,
                },
                "triggered_by": {
                    "type": "string",
                    "enum": ["auto", "user_request"],
                    "description": (
                        "BL-MM9-fix (5/9): 区分两种触发场景, 决定 evidence_count 校验严不严.\n"
                        "  - 'auto': 你自己观察员工反复做后主动 propose. 必须 evidence_count ≥3 防骚扰.\n"
                        "  - 'user_request': 员工**明确说**'封装为 skill' / '存成 skill' / '做成 skill', "
                        "你跟着调. evidence_count ≥1 即可.\n"
                        "鸿波 5/9 反馈: '下午我主动让鲶鱼生成 SKILL, 为什么不能生成, 很不合理'. "
                        "员工显式触发不该卡 3 次门槛."
                    ),
                    "default": "auto",
                },
                "triggers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "P3.5.43 (6/20): 触发关键词 list, 3-20 个. hermes 加载 skill 后注入到 "
                        "system prompt, LLM 看到员工说这些词就调 skill. 例: "
                        "['周报', '本周工作', '本周总结', '一周工作', 'weekly report']. "
                        "太少 (<3) 漏触发, 太多 (>20) 占预算 — 严守 3-20."
                    ),
                    "minItems": 3,
                    "maxItems": 20,
                },
                "kind": {
                    "type": "string",
                    "enum": ["procedural", "instructional"],
                    "description": (
                        "P3.5.43 (6/20): skill 类型. 多步操作流程 (录屏类) 选 procedural, "
                        "解释/教学/参考类选 instructional. hermes frontmatter 必填."
                    ),
                    "default": "procedural",
                },
                "skill_namespace": {
                    "type": "string",
                    "enum": ["personal", "department", "public", "creative"],
                    "description": (
                        "P3.5.43 (6/20): skill 装机 namespace. 老 hardcode personal, "
                        "现在让 LLM 按 skill 性质选: 员工本人偏好/工具 → personal, "
                        "整个部门共用 → department, 全公司公开 → public, 设计/文创 → creative."
                    ),
                    "default": "personal",
                },
            },
            "required": ["name", "reason", "action_steps", "evidence_count"],
        },
        "emoji": "💡",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── P3.5.43 (鸿波 6/20) — install_proposal 一键装 hermes-兼容 SKILL ──
    {
        "name": "catfish_install_proposal",
        "description": (
            "把 propose_skill 提案过的 skill **真装**到 hermes (员工 accept 后调). "
            "内部用 proposal jsonl 的 triggers/kind/description/action_steps 字段, "
            "走 skill_format 模板生成 hermes-兼容 SKILL.md + script.py + sync 到 "
            "~/.hermes/skills/<slug>/. 比 catfish_skill_install 自动化 — 不用 LLM "
            "重新拼 SKILL.md.\n\n"
            "✅ 调用场景: 员工说 'yes / 装吧 / 行 / 同意 / 安装' 等 accept 信号后, "
            "立即调本工具传 propose_skill 返回的 proposal_id.\n\n"
            "❌ 不该调:\n"
            "  - 员工没明确 accept (静默 / reject 都不装)\n"
            "  - proposal_id 不存在 (本工具会报错)\n\n"
            "**返**: {ok, skill_dir, hermes_dir, skill_name, namespace, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": "propose_skill 返回的 proposal_id (例: prop_1781923456_my-skill)",
                },
                "sync_to_hermes": {
                    "type": "boolean",
                    "description": "默认 True. 设 False 仅落 ~/.catfish/skills/ 不同步到 hermes (测试用)",
                    "default": True,
                },
            },
            "required": ["proposal_id"],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM13 (5/8) — 老 skill 自进化: propose 改进版本 ──
    {
        "name": "catfish_propose_skill_revision",
        "description": (
            "提议**修改一个已存在的 skill** (基于 audit log + BL-MM11 员工反馈观察到的问题). "
            "跟 catfish_propose_skill (抽**新** skill) 区别 — 这条改**老** skill 内容. "
            "**不直接改**, 等员工 accept 才落地, 跟 BL-MM9 一脉相承.\n\n"
            "✅ 调用场景:\n"
            "  - 员工 BL-MM11 给某 skill ≥2 个 👎 + 改动评论 ('太啰嗦' / '少这一步') → propose revision\n"
            "  - skill audit 失败率 ≥30% 持续 5 次 (员工反复重试同 skill) → propose 加 try-catch\n"
            "  - 员工 BL-MM12 综合质量分 < 40 (差) 持续 7 天 → propose 重写\n\n"
            "❌ 不该调用:\n"
            "  - 员工没反馈 / skill 用得少 (<5 次) → 数据不够, 静默\n"
            "  - 红线: 健康 / 财务 / 感情 / 政治 / 宗教 namespace skill 永不 propose 改\n"
            "  - 员工已经 reject 过同 skill 的 revision (24h 内) — 别骚扰\n\n"
            "**调用后**: 写 ~/.catfish/skill_revisions.jsonl, append 一条. 员工 Dashboard "
            "SkillRevisionCard 能看到, 点 ✅ 采纳 → catfish 调 BL-MM3 备份老版 → 写新版到 "
            "skill_path 下. 点 ❌ 拒绝 → 标 dismissed, 24h 内不再 propose.\n\n"
            "**返**: {ok, revision_id, summary}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": (
                        "要改的 skill 路径 (相对 catfish/skills/), kebab-case 命名空间. "
                        "例: 'department/weekly-report' / 'department/leadership-briefing'."
                    ),
                },
                "current_version": {
                    "type": "string",
                    "description": (
                        "当前 skill 版本号 (从 SKILL.md frontmatter 读). 例 '0.3.2'. "
                        "防 LLM 拿到 stale skill 内容做改, 跟 catfish 实际版本不一致."
                    ),
                },
                "proposed_version": {
                    "type": "string",
                    "description": (
                        "提议的新版本号 (SemVer bump). 大改 → minor (0.3.2 → 0.4.0); 修 bug → patch "
                        "(0.3.2 → 0.3.3); 不向后兼容 → major. 必须 > current_version."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "为什么改 — 必含**具体观察证据**: BL-MM11 反馈 / audit 失败 / 质量分. "
                        "例: '员工 5/8 5/9 5/10 三次 👎 + 评论 \"太啰嗦\", 看 audit 4 次 timeout '"
                        "原因没 catch InvalidArgument. 改: 删第 3 段 + 加 try-except.' "
                        "员工看了能直接确认或反驳, ≥30 字."
                    ),
                },
                "diff_summary": {
                    "type": "string",
                    "description": (
                        "改动概览 (3-8 句 markdown bullets, 让员工一眼看明白). "
                        "例: '- SKILL.md: 删第 3 段冗余说明\\n"
                        "- script.py: render_xxx 加 try/except 兜 InvalidArgument\\n"
                        "- 输出: 不再含 \"附件 (供参考)\" 那段员工说没用'. "
                        "完整 patch 在 LLM 后续生成 SKILL.md/script.py 时给, 这里只做 summary."
                    ),
                },
                "evidence_summary": {
                    "type": "string",
                    "description": (
                        "数据依据汇总: feedback 多少条 (👎 N, 评论 M) / audit 失败几次 / "
                        "质量分趋势. 例: 'BL-MM11: 5 个 👎 / 3 个改动评论. audit: 12 次调用 4 次失败 (33%). "
                        "BL-MM12 score: 35 (差) 持续 9 天.'"
                    ),
                },
            },
            "required": [
                "skill_path",
                "current_version",
                "proposed_version",
                "reason",
                "diff_summary",
                "evidence_summary",
            ],
        },
        "emoji": "🔧",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-D2 (5/10) Skills Hub publish ─────────────────────────────
    {
        "name": "catfish_skill_publish",
        "description": (
            "★ 把员工本机的 skill 发布到中央 Skills Hub (全公司共享).\n\n"
            "✅ 调用时机:\n"
            "  - 员工说'把这个 skill 发布到 hub' / '分享给团队'\n"
            "  - 你观察员工把同一 skill 改了 ≥ 3 次稳定后, propose 发布\n"
            "  - 员工 confirm 后才调 (跟 user_profile 同纪律, 不静默自决)\n\n"
            "input:\n"
            "  - skill_path: 本机 skill 目录, 必须含 SKILL.md (例 ~/.hermes/skills/my-skill)\n"
            "  - namespace: hub 上分类 (例 'department' / 'personal' / 'finance')\n"
            "    用员工部门时, 找 catfish_today_summary 的 department 字段\n"
            "  - auto_scrub_pii / auto_scrub_intranet (P3.3.17): 自动脱敏开关\n\n"
            "成功返:\n"
            "  {ok:true, namespace, name, version, published_at, hub_url, scrub_summary?}\n"
            "失败返:\n"
            "  {ok:false, error, scan_phase?, auto_scrub_available?}\n\n"
            "❌ 别在没员工 explicit 确认时调用. 别把含敏感 path / 凭据的 skill 发上去.\n\n"
            "🔁 失败 + auto_scrub_available=true 的处理 (P3.3.17, 6/10):\n"
            "  scan_phase=pii / intranet 命中时, error 给员工看 (含具体撞到啥),\n"
            "  问员工 '要不要让我自动把这些 PII / 内网地址替换成占位, 装上的同事自己填?'.\n"
            "  员工同意 → 再调本工具 with auto_scrub_pii=true (或 auto_scrub_intranet=true).\n"
            "  scrub 只改 hub 上传副本, 员工本机文件不动. SKILL.md 自动加 params: 段.\n"
            "  scan_phase=credentials 永不 auto_scrub — 凭据要员工本机手动改成 keychain:// ref.\n\n"
            "底层: 走 gateway /v1/hub/skills/{namespace} POST multipart, 跟 mcp-registry 同套 OIDC 鉴权."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "本机 skill 目录路径, 必须含 SKILL.md",
                },
                "namespace": {
                    "type": "string",
                    "description": "hub 上 namespace (例 'department' / 'personal')",
                },
                "auto_scrub_pii": {
                    "type": "boolean",
                    "description": (
                        "P3.3.17 (6/10): 命中 PII (身份证 / 手机号 / 工号 / 银行卡) 时, "
                        "true=自动替换成 {{phone_1}} 等占位 + 注入 SKILL.md frontmatter "
                        "params: 段; false=拒上传 (默认). 第一次 publish 撞到 PII 时, "
                        "先把 error 给员工看 + 问员工同意, 同意后再加这个参数 retry."
                    ),
                    "default": False,
                },
                "auto_scrub_intranet": {
                    "type": "boolean",
                    "description": (
                        "P3.3.17 (6/10): 命中内网 URL (10.x.x.x / 192.168.x.x / *.corp / "
                        "eis.* / oa.* 等) 时, true=自动替换成 {{INTRANET_EIS_1}} 等占位 + "
                        "frontmatter params; false=拒 (默认). 同 auto_scrub_pii, 第一次失败后跟员工确认再 retry."
                    ),
                    "default": False,
                },
            },
            "required": ["skill_path", "namespace"],
        },
        "emoji": "🚀",
        "toolset": "catfish_native",
        "available": True,
    },
    # ════════════════════════════════════════════════════════════
    # BL-MM9-FREEZE-v2 (5/12 鸿波拍板): 教学→凝固→复用闭环 (显式 session 边界)
    # ════════════════════════════════════════════════════════════
    {
        "name": "catfish_teach_start",
        "description": (
            "★★★ **开始一次教学 session** (BL-MM9-FREEZE-v2 5/12).\n\n"
            "员工说'我要教你 X' / '教你做 Y' / '记一下接下来的步骤' / "
            "'凝固成 skill 之前我先教你跑一遍' → **第一件事调本工具**.\n\n"
            "**核心机制**: 没 active teach session 时, 你调任何 "
            "catfish_browser_* / catfish_recognize_captcha / catfish_browser_locate "
            "都**不会被录**. 调本工具后 → 进入教学模式 → 每个业务工具 call 都进"
            "trace → 最终凝固成 skill 的 step.\n\n"
            "✅ 调用场景:\n"
            "  - '我教你登 EIS' → catfish_teach_start(name='eis-login')\n"
            "  - '记一下接下来怎么走 OA 审批' → catfish_teach_start(name='oa-approval')\n"
            "  - 任何'员工指挥你跑一遍, 之后要凝固成 skill'的场景\n\n"
            "❌ 不要调用:\n"
            "  - 员工只是问问题 / 不教学 → 不调\n"
            "  - 你已经在 active session 里 (老 session 会被自动关掉, 但浪费)\n"
            "  - 复用阶段 (调 catfish_run_skill) — 那是用 skill, 不是教 skill\n\n"
            "**教学纪律**: 调完本工具后, 每个 tool call 都进 trace. **不要做无关"
            "探索** (e.g. 'snapshot 看看页面长啥样') — 那会进凝固 skill. "
            "只跑员工 explicit 指挥的步骤. 不确定就先**问员工**, 别自己探.\n\n"
            "**返回**: {ok, session_id, name, started_at_iso, summary}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "skill 名, 小写字母数字横线 (例 'eis-login'). 凝固时同名."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "教学目的简介 (1-200 字), 给后续凝固时元数据用.",
                },
            },
            "required": ["name"],
        },
        "emoji": "🎓",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_teach_end",
        "description": (
            "★★ **结束当前教学 session** (BL-MM9-FREEZE-v2 5/12).\n\n"
            "员工说'教完了' / '就这些' / '可以凝固了' / 类似收尾意图 → 立刻调.\n\n"
            "**作用**:\n"
            "  - 归档当前 active.jsonl 到 session_<name>_<ts>.jsonl\n"
            "  - 写 _last_completed.json 让 catfish_freeze_skill 能找到\n"
            "  - 清除 active 状态 — 后续 tool call 不再被录\n\n"
            "✅ 调用时机:\n"
            "  - 员工 explicit 说教完了 / 可以凝固\n"
            "  - 教学的最后一步完成后, 员工没说继续 — 主动问'教完了吗?', "
            "员工确认就调\n\n"
            "❌ 不要调用:\n"
            "  - 没 active session — 调了会返 error\n"
            "  - 教学中途, 员工没 explicit 说结束\n\n"
            "**返回**: {ok, session_id, name, step_count, duration_s, archive_path, summary}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "结束原因 (e.g. 'done' / 'aborted'), 进归档元信息.",
                },
            },
            "required": [],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_inspect",
        "description": (
            "★ 查 trace 状态. 教学过程中员工想知道'我刚才让鲶鱼做的几步, 系统"
            "都录下来了吗', 调这个看. 返回 trace 文件大小 / 最近窗口内的步骤"
            "数 / 每个 tool 的调用次数. 凝固前先调一次, 确认 trace 长度合理.\n\n"
            "✅ 调用场景:\n"
            "  - 员工说'刚才教的几步录下来了吗?' → catfish_freeze_inspect\n"
            "  - 凝固前 sanity check\n\n"
            "**参数**: since_unix (可选, 默认 1 小时前). \n"
            "**返回**: {ok, trace_file{lines/tools/...}, recent_steps_summary[...]}"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since_unix": {
                    "type": "number",
                    "description": "起始 unix 时间戳 (秒). 默认 1 小时前.",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_skill",
        "description": (
            "★★★ **凝固最近一个完成的 teach session** 成可执行 skill (BL-MM9-FREEZE-v2).\n\n"
            "**前置条件**: 必须先 catfish_teach_start → 教学 → catfish_teach_end "
            "→ 才能 catfish_freeze_skill. 没 session 直接凝固会拒绝.\n\n"
            "✅ 调用时机:\n"
            "  - catfish_teach_end 调完后, 员工说'凝固成 skill'\n"
            "  - 员工 explicit 给了 name + description\n\n"
            "❌ 不该调用:\n"
            "  - active session 还没 end → 拒\n"
            "  - 没有 last_completed session → 拒\n"
            "  - 旧 session trace 有 fail step / 包含 LLM 探索 → 调本工具前\n"
            "    应该让员工**重教一次**, 把干净的 8 步教明白\n\n"
            "**参数**:\n"
            "  - name: 'eis-login' 等. 跟 catfish_teach_start 传的一致就行.\n"
            "  - namespace: 'department' (默认) / 'personal' / 'team'\n"
            "  - description: 1-500 字描述\n"
            "  - target: 'local' (默认, 5/21 加, 落 ~/.catfish/skills/) / 'workspace' (落工程目录, 业务 skill)\n"
            "  - overwrite: 同名 skill 已存在时是否覆盖 (默认 false)\n"
            "  - run_install: 凝固后自动跑 install_to_hermes.sh (5/21 默认 false. 只 target='workspace' 生效)\n\n"
            "**返回**: {ok, name, namespace, target, skill_path, skill_dir, hermes_name, files[], params[], "
            "register_external_dir{...}, install{...}, summary}\n\n"
            "**5/21 方案 1 隐私纪律**:\n"
            "  - 教学产物默认 target='local' 落本机 ~/.catfish/skills/, **永不**自动 publish 中央 Hub.\n"
            "  - 想发布到团队 → 员工显式点 Companion UI 按钮, 走 catfish_skill_publish (跑 3 道扫描: 凭据 / PII / 内网 URL).\n"
            "  - LLM **不要**自己调 catfish_skill_publish 当 freeze 一部分.\n\n"
            "**安全**:\n"
            "  - trace 里 fill 含明文密码 → 拒凝固, 提示员工用 secret_ref 重教\n"
            "  - secret_ref 原样保留在 script.py (不解析成明文)\n"
            "  - captcha 识别结果 hard-code 自动改成实时调用"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名, 小写字母数字横线 (例 'eis-login'). 字母开头.",
                },
                "namespace": {
                    "type": "string",
                    "enum": ["department", "personal", "team"],
                    "description": "namespace, 默认 department.",
                },
                "description": {
                    "type": "string",
                    "description": "skill 描述, 1-500 字, 进 SKILL.md frontmatter.",
                },
                "target": {
                    "type": "string",
                    "enum": ["local", "workspace"],
                    "description": (
                        "落盘路径 (5/21 加). 'local' (默认): ~/.catfish/skills/, 教学私有, 自动注册到 "
                        "hermes external_dirs, Curator 不动. 'workspace': ~/person_task/catfish/skills/, "
                        "业务 skill 源码工程目录用, 配合 install_to_hermes.sh."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "已存在的 skill 是否覆盖. 默认 false.",
                },
                "run_install": {
                    "type": "boolean",
                    "description": "凝固后自动跑 install_to_hermes.sh. 5/21 默认 false. 仅 target='workspace' 生效.",
                },
                "session_archive_path": {
                    "type": "string",
                    "description": "调试用 — 显式指定某 session archive 文件路径. 一般不传.",
                },
            },
            "required": ["name"],
        },
        "emoji": "🧊",
        "toolset": "catfish_native",
        "available": True,
    },
    {
        "name": "catfish_freeze_rotate",
        "description": (
            "凝固完一个 skill 之后, 把 active trace 文件归档 (重命名带时间戳), "
            "开始空白的新 trace. 防下次教学跟上次混. 通常在 catfish_freeze_skill "
            "成功后调一次.\n\n"
            "**参数**: reason (可选, e.g. 'post-freeze-eis-login')"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "归档理由 (进归档文件名).",
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-SKILLS-RAG-TOOL (5/25 鸿波 "现在做") Progressive Disclosure 折叠区主动捞 ──
    {
        "name": "catfish_search_skills",
        "description": (
            "★★★ 跨 ~/.hermes/skills + ~/.catfish/skills BM25 搜 skill — "
            "**当 system prompt 里 skill catalog 折叠了 N 个**(显示 '还有 N 个 skill, "
            "想用调 catfish_search_skills') **必用这个找**.\n\n"
            "✅ 调用场景:\n"
            "  - system prompt 折叠区显示 'hermes:bundled 还有 168 个 skill' + 员工说 "
            "'帮我做 ECharts 图' → query='ECharts 图表' (上方 catalog 没看到 echarts 类 skill)\n"
            "  - 员工说 '有没有快速生成发票模板的 skill?' → query='发票模板 生成'\n"
            "  - 员工说 '我想找跟 GitHub 同步的工具' → query='GitHub 同步'\n"
            "  - 员工模糊问 '能帮我搞个 X 吗', 你 catalog 里没匹配 → 主动 search\n\n"
            "❌ 不调用:\n"
            "  - catalog 里 inline 显示的 skill (前 ~15 个 BM25 top-K) — 直接 catfish_run_skill\n"
            "  - 已知 skill_path 想要参数 → catfish_run_skill(skill_path='...', params={'_help': True})\n"
            "  - 找历史会话 → catfish_search_sessions\n"
            "  - 找邮件 → catfish_email_search\n\n"
            "返参:\n"
            "  - matches: top-K skill 列表 {skill_path, name, description 摘要, namespace, score}\n"
            "  - count: 命中数\n"
            "  - total_indexed: 本机共扫到多少 skill\n"
            "  - summary: 一句话归纳 + 建议下一步 (e.g. '找到 5 个, 调 catfish_run_skill _help 拿参数')\n"
            "  - latency_ms\n\n"
            "🔒 隐私: 直读员工 mac 本机 ~/.hermes/skills + ~/.catfish/skills, 不走 gateway, "
            "不上行中央, 不跨员工.\n\n"
            "💡 思路 (Anthropic Progressive Disclosure 3 层):\n"
            "  Tier 1 (system prompt catalog) - 你已看见 top-K\n"
            "  Tier 2 (这工具) - 折叠区 BM25 搜 + 拿 description 摘要\n"
            "  Tier 3 (catfish_run_skill _help) - 决定调时拿完整 SKILL.md 参数 schema"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜的关键字 / 自然语言 ('ECharts 图表' / '发票模板' / 'GitHub 同步' 等)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返多少个 (默认 10, 上限 30). 默认够用, 真没匹配再加大.",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔧",
        "toolset": "catfish_native",
        "available": True,
    },
]
