E7.P1 ship: skill/MCP 视觉分层 + isProtected 标 (phase 1)

鸿波 6/6 看仪表盘问"已装的 skill/MCP 是不是可以支持自己安装或者删除". 当前是
read-only 纯展示卡, 跟 catfish "数据零出端 / 员工主权 / 中央 0 红线" 3 条核心
价值观有 gap. 分 phase ship — phase 1 先把"哪些能动 / 哪些核心" 视觉分清,
phase 2 才接 install/uninstall subprocess (留下次, 涉及 npx subprocess + trash
目录 + undo toast, 单独 ship).

phase 1 = 视觉分层 + isProtected 后端标:

Rust (commands/skills.rs):
  - SkillEntry 加 is_protected: bool (catfish 仓库 skills/ = true 不可删,
    ~/.hermes/skills/ + ~/.catfish/skills/ = false)
  - McpServerEntry 加 is_protected: bool (catfish-* 前缀 = true, 主链路核心)
  - parse_skill_md → parse_skill_md_with_protected (老函数留 backward-compat
    wrapper + #[allow(dead_code)])
  - scan_skills_root_filtered → scan_skills_root_filtered_full(... is_protected: bool)
  - list_installed_skills_blocking 给 catfish 仓库树传 is_protected=true,
    ~/.hermes/ 默认 false
  - list_mcp_servers_blocking 按 name.starts_with("catfish-") 计算

TS (types/identity.ts):
  - SkillEntry / McpServerEntry interface 加 isProtected: boolean

UI (tabs/Dashboard/SkillsMcpCard.tsx 重写, globals.css +205 行):
  - 删 inline style + 1px border + grid-column 2 个 hardcoded
  - 走 .dashboard-skills__* className 群 (E5 taste-skill 改造 brand 一致)
  - 2 段子分组 SubGroup:
    * "团队审定 · N 个" — locked, ◆ 锁标 + muted gray bg, hover tooltip
      "由 catfish 仓库 / 主链路管控, 不可删"
    * "我装的 · M 个" — 正常 bg-cream, 右侧预留卸载 slot (phase 2 接)
  - skill 按 isProtected 粒度拆 (不是 namespace 粒度) — 一个 namespace 可能混
    catfish 仓库审定 + 员工后装 (e.g. productivity 有 catfish-* protected +
    自装 skill)
  - MCP 同模式 (catfish-tools 锁 / 员工后接的可删)
  - namespace 折叠 caret rotate 90deg (跟 E3 toolcall / E4 WikiTree group / E5
    WikiPreview history 一致 pattern, 复用)
  - 顶部 stats 1px border → 渐变 1px gradient (跟 Wiki history 同 subtle 分隔)
  - keyboard a11y: role=button + Enter/Space + aria-expanded + focus-visible ring

CI: tsc --noEmit 全过. cargo check sandbox 无 toolchain, 改动 mechanical
(加 bool field + 透传), tests 全部不碰 SkillEntry literal (只 test
is_recmode_frozen_skill), 鸿波本地 cargo build 时验证.

phase 2 backlog (E7 #37/#38):
  - install_skill_from_url(url) — npx skills add subprocess
  - uninstall_skill(skill_path) — 移到 ~/.catfish/.trash/skills/<ts>/
  - restore_skill(trash_path) — 5 秒 undo toast
  - add_mcp_server(name, command, args) — 改 config.yaml
  - 安装对话框 + 卸载二次确认 + undo toast UI

清理: 删 .git-commit-E.msg (E5 用过).
