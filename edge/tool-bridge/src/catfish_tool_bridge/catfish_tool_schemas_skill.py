"""skill 的安装 / 提议 / 冻结 / 教学 / 发布 —— 14 个 tool 的 JSON Schema。

8/15 从 catfish_tool_schemas.py 拆出: 那个文件是**一整个 3362 行的
列表字面量** (78 个 schema), 越过 800 红线 4 倍。零逻辑、零变量引用,
所以按类别切成 8 份, 由 catfish_tool_schemas.py 拼回去。

加新 tool: schema 加到对应类别文件, impl 加 catfish_tools.py 或子 module,
dispatch 加 catfish_tools._dispatch_native_inner —— **dispatch 不重组**
(这条红线是 5/20 拆分时定的, 见 catfish_tool_schemas.py 文件头)。

description 每次请求都发给模型 (所有 tool 合计曾 ~37K token, 首字 15-30s),
所以只写「做什么 / 何时用 / 与邻居的区别 / 安全规则 / 参数格式」。
历史、编号、实现细节写在 # 注释里, 不进 description。
"""
from __future__ import annotations

from typing import Any, Dict, List


SKILL_TOOLS: List[Dict[str, Any]] = [
    # 备份落 ~/.hermes/skills/<ns>/<skill>/.versions/<unix-ts>.md;
    # 不先备份直接 skill_manage update 会被 catfish-policy R10 deny。
    # 返回 {ok, backup_path, version_count}。
    {
        "name": "catfish_skill_backup",
        "description": (
            "备份一个 skill 的当前 SKILL.md 到 .versions/. "
            "skill_manage(action=update/delete) 之前**必须**先调, 否则会被拒; 新建 skill 不用调. "
            "员工说'回退 X skill'时可从 .versions/ 取最近一版."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "'namespace/skill', 例 'productivity/expense-submit'",
                },
                "reason": {
                    "type": "string",
                    "description": "为啥要改/删, 一句话 (员工可见)",
                },
            },
            "required": ["skill_name", "reason"],
        },
        "emoji": "💾",
        "toolset": "catfish_native",
        "available": True,
    },
    # 失败铁律来自 BL-MM9-FREEZE-v2 (5/12): skill 里的 selector 是教学时验证过的,
    # 模型手工推的 selector 不可靠, 会污染 chrome 状态。tests/test_run_skill_reports_failure.py
    # 和 skill_format.py 都依赖这条规则 (ok=false → 报告员工, 不手工接管)。
    # 返回 {ok, files, summary, error}, files 自动渲染成 pill。
    {
        "name": "catfish_run_skill",
        "description": (
            "调用已审定的 skill (含凝固的浏览器自动化 skill、渲染类 skill). "
            "skill 列表里有匹配的就**立即**调, 优先于自己写代码或手工调 catfish_browser_*. "
            "不知道参数时传 params={'_help': True} 拿 schema.\n"
            "工作流 skill 返回 status: done / waiting_confirm (员工要在鲶鱼界面点确认, 你不能替他确认) / "
            "failed (说清第几步和原因, 可 catfish_workflow(action=resume, run_id) 续跑).\n"
            "**失败铁律**: 返 ok=false 时**绝对不要**自己调 catfish_browser_* 手工接管; "
            "要把 skill 名和 error 告诉员工, 问'重试 / 重教 / 手工接管?', 员工明确说手工才允许."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "例 'department/leadership-briefing', 不带首尾 /",
                },
                "params": {
                    "type": "object",
                    "description": "skill 入参; 不确定时传 {'_help': True}",
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
    # 安装规则: SKILL.md 必须 (script.py 可选); frontmatter name 决定 <namespace>/<name>/;
    # 安装后自动 dry-run + audit (~/.catfish/skill_audit.jsonl event_type=install)。
    # hub_url 默认 env CATFISH_HUB_URL, 未设则 http://127.0.0.1:9001。
    # 返回 {ok, installed_path, error, source: 'local'|'hub'}。
    {
        "name": "catfish_skill_install",
        "description": (
            "员工明确要求安装 skill 时调用. 两种来源二选一: "
            "source_dir (本机目录, 须含 SKILL.md; 拒绝 /etc、/usr 等系统目录) 或 "
            "hub_skill (中央 Skills Hub, 'namespace/name@version', version 可写 latest). "
            "同名已存在须 overwrite=true. 安装 propose_skill 的提案用 catfish_install_proposal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_dir": {
                    "type": "string",
                    "description": "本机目录绝对路径或 ~ 开头, 与 hub_skill 二选一",
                },
                "hub_skill": {
                    "type": "string",
                    "description": "'namespace/name@version', 例 'shared/x@latest'",
                },
                "hub_url": {
                    "type": "string",
                    "description": "Hub 地址, 一般不传",
                },
                "namespace": {
                    "type": "string",
                    "description": "本机 namespace, 默认 personal; hub 模式默认用 hub_skill 自带的",
                    "default": "personal",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "同名时覆盖 (自动备份旧版)",
                    "default": False,
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # 五一 sprint Day 2 加。删前自动备份到 ~/.catfish/skill-trash/<unix-ts>/;
    # audit event_type=delete。已加载 module 仍在 sys.modules, 新 session 才消失。
    # 返回 {ok, deleted_path, backup_path, error}。
    {
        "name": "catfish_skill_delete",
        "description": (
            "删除一个 skill 目录 (不可逆, 自动备份到回收站 30 天内可恢复). "
            "**只在员工明确说删时调用**; 员工只说'看不到这个 skill'不是要删. "
            "新会话或重启 Companion 后生效."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "同 catfish_run_skill 的 skill_path",
                },
                "reason": {
                    "type": "string",
                    "description": "删除原因, 写审计",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "必须 true; 员工没明确说删就不能自己填 true",
                },
            },
            "required": ["skill_path", "reason", "confirm"],
        },
        "emoji": "🗑",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-MM9 (5/8) — agent 自动抽 skill ──
    # 写 ~/.catfish/skill_proposals.jsonl (event_type=propose)。返回 {ok, proposal_id, total_proposals, summary}。
    # BL-MM9-fix (5/9): 员工显式要求「存成 skill」时不卡 3 次门槛 (triggered_by=user_request)。
    # P3.5.43 (6/20): 加 triggers / kind / skill_namespace, 供 install_proposal 生成 hermes 兼容 SKILL.md。
    {
        "name": "catfish_propose_skill",
        "description": (
            "把员工反复做的流程**提案**成新 skill, 不直接装. "
            "调用时机: 你观察到同类任务 ≥3 次 (triggered_by='auto'), 或员工明确说'存成 skill / 做成 skill' (triggered_by='user_request'). "
            "不调: 只做过 1-2 次; 健康/财务/感情/政治/宗教类永不提案; 员工已拒绝过同类. "
            "调用后问员工'要不要存成 skill?', 员工同意再调 catfish_install_proposal. "
            "改已有 skill 用 catfish_propose_skill_revision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "kebab-case, 例 'travel-expense-calc'",
                },
                "reason": {
                    "type": "string",
                    "description": "1-2 句, 含具体观察证据",
                },
                "action_steps": {
                    "type": "string",
                    "description": "3-5 步 markdown bullets",
                },
                "evidence_count": {
                    "type": "integer",
                    "description": "观察到的次数; auto 须 ≥3, user_request ≥1",
                    "minimum": 1,
                },
                "triggered_by": {
                    "type": "string",
                    "enum": ["auto", "user_request"],
                    "description": "auto=你主动观察; user_request=员工明确要求",
                    "default": "auto",
                },
                "triggers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-20 个触发关键词, 例 ['周报', '本周总结', 'weekly report']",
                    "minItems": 3,
                    "maxItems": 20,
                },
                "kind": {
                    "type": "string",
                    "enum": ["procedural", "instructional"],
                    "description": "多步操作流程选 procedural, 解释/参考类选 instructional",
                    "default": "procedural",
                },
                "skill_namespace": {
                    "type": "string",
                    "enum": ["personal", "department", "public", "creative"],
                    "description": "personal 个人 / department 部门共用 / public 全公司 / creative 设计类",
                    "default": "personal",
                },
            },
            "required": ["name", "reason", "action_steps", "evidence_count"],
        },
        "emoji": "💡",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── P3.5.43 (6/20) — install_proposal 一键装 hermes-兼容 SKILL ──
    # 内部用 proposal jsonl 的 triggers/kind/description/action_steps, 走 skill_format 模板
    # 生成 SKILL.md + script.py 并 sync 到 ~/.hermes/skills/<slug>/。
    # 返回 {ok, skill_dir, hermes_dir, skill_name, namespace, summary}。
    {
        "name": "catfish_install_proposal",
        "description": (
            "把 catfish_propose_skill 的提案真正装成 skill. "
            "**只在员工明确同意** ('装吧 / 行 / 同意') 后调, 传提案返回的 proposal_id; 员工沉默或拒绝不装. "
            "比 catfish_skill_install 省事, 不用重新拼 SKILL.md."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": "例 'prop_1781923456_my-skill'",
                },
                "sync_to_hermes": {
                    "type": "boolean",
                    "description": "默认 true; false 仅测试用",
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
    # 证据来源: BL-MM11 员工 👍/👎 反馈、skill audit 失败率、BL-MM12 质量分。
    # 写 ~/.catfish/skill_revisions.jsonl; 员工在 Dashboard SkillRevisionCard 采纳 →
    # 先备份老版再写新版; 拒绝 → 24h 内不再提。返回 {ok, revision_id, summary}。
    {
        "name": "catfish_propose_skill_revision",
        "description": (
            "提议修改一个**已存在**的 skill (新 skill 用 catfish_propose_skill). 不直接改, 员工采纳才落地. "
            "调用时机: 员工对某 skill 多次差评或提改动意见; 该 skill 反复失败 (失败率 ≥30%); 质量分长期偏低. "
            "不调: 使用 <5 次数据不够; 健康/财务/感情/政治/宗教类; 员工 24h 内拒绝过同 skill 的修改."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "例 'department/weekly-report'",
                },
                "current_version": {
                    "type": "string",
                    "description": "SKILL.md frontmatter 里的版本, 例 '0.3.2'",
                },
                "proposed_version": {
                    "type": "string",
                    "description": "SemVer, 须大于 current; 修 bug→patch, 大改→minor",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么改, 含具体证据, ≥30 字",
                },
                "diff_summary": {
                    "type": "string",
                    "description": "3-8 条 markdown bullets 概述改动",
                },
                "evidence_summary": {
                    "type": "string",
                    "description": "差评数 / 失败次数 / 质量分趋势",
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
    # 底层 gateway POST /v1/hub/skills/{namespace} multipart, OIDC 鉴权。
    # P3.3.17 (6/10): 扫描 (凭据 / PII / 内网 URL) 命中时返 {ok:false, error, scan_phase,
    # auto_scrub_available}; scrub 只改上传副本, 并在 SKILL.md 加 params: 段。
    # 成功返 {ok, namespace, name, version, published_at, hub_url, scrub_summary?}。
    {
        "name": "catfish_skill_publish",
        "description": (
            "把员工本机 skill 发布到中央 Skills Hub 供全公司共享. "
            "**员工明确确认后才调** (例: '发布到 hub' / '分享给团队'). 不要把含凭据或敏感路径的 skill 发上去. "
            "若失败且 auto_scrub_available=true (scan_phase=pii/intranet): 把 error 给员工看, "
            "问是否自动替换成占位; 同意后带 auto_scrub_pii 或 auto_scrub_intranet=true 重试. "
            "scan_phase=credentials 永不自动脱敏, 让员工本机改成 keychain:// 引用."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_path": {
                    "type": "string",
                    "description": "本机 skill 目录, 须含 SKILL.md",
                },
                "namespace": {
                    "type": "string",
                    "description": "例 'department'; 部门名见 catfish_today_summary",
                },
                "auto_scrub_pii": {
                    "type": "boolean",
                    "description": "PII 替换成占位; 仅员工同意后重试时设 true",
                    "default": False,
                },
                "auto_scrub_intranet": {
                    "type": "boolean",
                    "description": "内网地址替换成占位; 仅员工同意后重试时设 true",
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
    # BL-MM9-FREEZE-v2 (5/12 拍板): 教学→凝固→复用闭环 (显式 session 边界)
    # 没有 active teach session 时 browser/captcha 类 tool call 不进 trace。
    # ════════════════════════════════════════════════════════════
    # 返回 {ok, session_id, name, started_at_iso, summary}。
    {
        "name": "catfish_teach_start",
        "description": (
            "开始一次教学 session, 之后每个 tool call 都会被录下, 最终可凝固成 skill. "
            "员工说'我教你 X / 记一下接下来的步骤'时**第一件事调这个**. "
            "开始后只跑员工明确指挥的步骤, 不做无关探索 (会被录进 skill), 不确定先问员工. "
            "不调: 员工只是提问; 使用已有 skill (用 catfish_run_skill)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "小写字母数字横线, 例 'eis-login'",
                },
                "description": {
                    "type": "string",
                    "description": "教学目的, 1-200 字",
                },
            },
            "required": ["name"],
        },
        "emoji": "🎓",
        "toolset": "catfish_native",
        "available": True,
    },
    # 归档 active.jsonl → session_<name>_<ts>.jsonl, 写 _last_completed.json 供 freeze_skill。
    # 返回 {ok, session_id, name, step_count, duration_s, archive_path, summary}。
    {
        "name": "catfish_teach_end",
        "description": (
            "结束当前教学 session, 停止录制并归档, 供 catfish_freeze_skill 使用. "
            "员工说'教完了 / 就这些 / 可以凝固了'时调; 最后一步做完员工没说继续, 先问'教完了吗?'. "
            "教学中途员工没说结束不要调."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "例 'done' / 'aborted'",
                },
            },
            "required": [],
        },
        "emoji": "✅",
        "toolset": "catfish_native",
        "available": True,
    },
    # 返回 {ok, trace_file{lines/tools/...}, recent_steps_summary[...]}。
    {
        "name": "catfish_freeze_inspect",
        "description": (
            "查看教学 trace 录了哪些步骤 (步骤数、各 tool 调用次数). "
            "员工问'刚才教的录下来了吗?'或凝固前确认 trace 时调."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since_unix": {
                    "type": "number",
                    "description": "起始 unix 秒, 默认 1 小时前",
                },
            },
            "required": [],
        },
        "emoji": "🔍",
        "toolset": "catfish_native",
        "available": True,
    },
    # 5/21 方案 1 隐私纪律: target 默认 local (~/.catfish/skills/, 自动注册到 hermes
    # external_dirs); workspace 落工程目录 ~/person_task/catfish/skills/ 配合 install_to_hermes.sh。
    # 安全: secret_ref 原样保留不解析; captcha 结果 hard-code 自动改成实时调用。
    # 返回 {ok, name, namespace, target, skill_path, skill_dir, hermes_name, files[], params[],
    # register_external_dir{...}, install{...}, summary}。
    {
        "name": "catfish_freeze_skill",
        "description": (
            "把最近一个已结束的教学 session 凝固成可执行 skill. "
            "前置: 必须已 catfish_teach_start → 教学 → catfish_teach_end, 否则会拒绝. "
            "员工说'凝固成 skill'时调. trace 里有失败步骤或无关探索时, 先请员工重教一次. "
            "trace 含明文密码会被拒, 提示员工用 secret_ref 重教. "
            "凝固默认只存本机, **不要**顺手调 catfish_skill_publish; 发布由员工自己决定."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "小写字母数字横线, 字母开头; 同 teach_start 的 name",
                },
                "title": {
                    "type": "string",
                    "description": "给员工看的中文名, 4-12 字, 例「登录 EIS 门户」",
                },
                "namespace": {
                    "type": "string",
                    "enum": ["department", "personal", "team"],
                    "description": "默认 department",
                },
                "description": {
                    "type": "string",
                    "description": "1-500 字",
                },
                "target": {
                    "type": "string",
                    "enum": ["local", "workspace"],
                    "description": "默认 local (本机私有); workspace 仅开发业务 skill 时用",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "同名时覆盖, 默认 false",
                },
                "run_install": {
                    "type": "boolean",
                    "description": "凝固后自动安装, 仅 target='workspace' 生效, 默认 false",
                },
                "session_archive_path": {
                    "type": "string",
                    "description": "调试用, 一般不传",
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
            "归档当前 trace 并开始空白新 trace, 防止下次教学跟上次混在一起. "
            "通常在 catfish_freeze_skill 成功后调一次."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "例 'post-freeze-eis-login'",
                },
            },
            "required": [],
        },
        "emoji": "📦",
        "toolset": "catfish_native",
        "available": True,
    },
    # ── BL-SKILLS-RAG-TOOL (5/25) Progressive Disclosure 折叠区主动捞 ──
    # 本机 BM25 搜 ~/.hermes/skills + ~/.catfish/skills, 不走 gateway、不上行。
    # 三层: system prompt catalog (top-K) → 本工具 → catfish_run_skill _help。
    # 返回 {matches[{skill_path, name, description, namespace, score}], count, total_indexed, summary, latency_ms}。
    {
        "name": "catfish_search_skills",
        "description": (
            "在本机所有 skill 里按关键词搜索. system prompt 的 skill 列表折叠了 (显示'还有 N 个 skill'), "
            "或员工要的能力在列表里没找到时调, 例 query='ECharts 图表'. "
            "不调: 列表里已看到的 skill 直接 catfish_run_skill; 找历史会话用 catfish_search_sessions; "
            "找邮件用 catfish_email_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词或自然语言, 例 '发票模板'",
                },
                "top_k": {
                    "type": "integer",
                    "description": "默认 10, 上限 30",
                },
            },
            "required": ["query"],
        },
        "emoji": "🔧",
        "toolset": "catfish_native",
        "available": True,
    },
]
