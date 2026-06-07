E7.P2 ship: skill/MCP 自助安装 / 卸载 / undo toast (phase 2 闭环)

phase 1 (E7.P1) ship 视觉分层 + isProtected 后端标; phase 2 接通真 action
链路 — 员工可以自助装 / 删 skill 和 MCP, 不用开 terminal.

Rust (commands/skills.rs +260 行, 5 个新 tauri command):
  - install_skill_from_url(url) → spawn `npx -y skills add <url>` subprocess
    * URL 校验 (http(s):// / github: / @scope/pkg 4 种前缀, 拒任意 shell injection)
    * PATH augment — mac npx 常在 /usr/local/bin / /opt/homebrew/bin / ~/.nvm/versions/node/*/bin,
      不依赖员工 shell PATH 也能 spawn
    * 返 InstallResult { success, stdout, stderr, exitCode } 让 UI 显日志
  - uninstall_skill(skill_path) → 移到 ~/.catfish/.trash/skills/<ts>/<basename>/
    * 路径校验 (is_legal_skill_root): 必须 canonicalize 后在 ~/.hermes/ / ~/.claude/
      / ~/.catfish/ 下, 拒前端误传 /etc/passwd 等任意路径
    * 不真 rm, 移 trash, 返 trashPath + originalPath 给 5 秒 undo 用
  - restore_skill(trash_path, original_path) → fs::rename 反向
    * 校验 original 没被占 (防员工 5 秒内又装了同名 skill)
  - add_mcp_server(name, command, args) → 改 ~/.hermes/config.yaml mcp_servers 段
    * 拒 catfish-* 命名 (主链路核心保留)
    * serde_yaml 解 + insert + 序列化回写, 不破坏其它 yaml 段 (anchor / 注释除外)
  - remove_mcp_server(name) → 删 mcp_servers.<name>
    * 同拒 catfish-* (主链路依赖)

TS (lib/tauri.ts +40 行):
  - InstallResult / UninstallResult interface
  - installSkillFromUrl / uninstallSkill / restoreSkill / addMcpServer / removeMcpServer
    5 个 invoke wrapper

useIdentity.ts:
  - useInstalledSkillsAndMcp 加 reload() — bump 触发 useEffect 重跑 fetch
  - install/uninstall/addMcp/removeMcp 成功后调 reload() UI 立即反映

UI (SkillsMcpCard.tsx 大改 +450 行, globals.css +200 行):
  - 顶部 action row: "+ 安装 skill" / "+ 接入 MCP" 2 个 ghost link button
  - InstallSkillDialog 输 URL + 4 种前缀校验 + 错误提示
  - 安装中 modal: spinner + 命令显 + "不能关" 提示
  - 安装结果 modal: <details> 折叠 stdout / stderr + exit code, 失败时 stderr 默认展开
  - AddMcpDialog 输 name + command + args 三栏 + catfish-* 命名拒
  - 每个 unprotected skill / MCP 行 hover 显 "卸载" / "移除" 文字链 (opacity 0→1)
  - 卸载二次 confirm modal (描述 trash + undo 流程)
  - undo toast (bottom-right, 5 秒倒计时, "↻ 已卸载 <name> 撤销 5s")
    * skill 走 restoreSkill (fs::rename 回原位)
    * MCP 走 addMcpServer 重新写回 config.yaml (yaml 没法 atomic undo, 但记下
      原 name/command/args 让员工一键 re-add)
  - Dialog 容器: backdrop + ESC 关 + click 外关 + slide-in animation +
    role="dialog" aria-modal a11y

CSS class 群 (跟 E5 brand 一致):
  - .install-dialog* — modal + backdrop + input focus-visible 3px ring +
    spinner animation + stdout/stderr <pre> 风格 (跟 wiki textarea 同语言)
  - .undo-toast* — bottom-right 浮动 + slide animation + 倒计时
    + brand 墨青 ↻ icon + 撤销 button
  - .dashboard-skills__row-action — hover-reveal 卸载 link (opacity 过渡)
  - .dashboard-skills__top-actions — 顶部 action row 容器

安全设计原则 (跟 catfish "中央 0 红线 / 员工主权" 价值观对齐):
  - is_protected=true 的 skill / MCP, 前端不 render uninstall button,
    后端就算前端绕过也 hard reject (catfish-* MCP, catfish 仓库 skill)
  - skill 卸载走 trash 不真 rm (5 秒后 trash 留盘, 后台 cron 清, BL)
  - MCP 删后会提示员工"hermes daemon 要重启 MCP 才完全脱钩"
  - 安装走 npx subprocess (跟员工 terminal 装一致), 不引入新依赖

CI: tsc --noEmit 全过. cargo sandbox 无 toolchain, 改动验证: subprocess error
path / 路径校验 / yaml 改写 / fs::rename 都是 std::fs 标准模式, 鸿波本地
cargo build 时验证 (predicted no panic). serde_yaml 已经在 cargo.toml.

清理: 删 .git-commit-E7-P1.msg (E7.P1 用过).

E7 全套 (P1 + P2) 闭环 — 仪表盘 "已装的 skill / MCP" 现在不再是 read-only,
员工可以自助装 / 删, 锁标 ◆ 区分核心 / 可改, undo 5 秒兜底误操作.
