#!/usr/bin/env bash
# 5/4-5/7 sprint 自动 commit 脚本
# 用法: bash commit-sprint.sh
# 任何一步 fail 立即停, 不会半 commit 留下烂账
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .git/index.lock ]]; then
  echo "⚠️  .git/index.lock 还在, 先清:"
  echo "    rm -f .git/index.lock"
  exit 1
fi

echo "========== 检查工作目录 =========="
git status --short | wc -l
echo

# ---------------------------------------------------------------------
# Commit 1 · BL-MM7 + BL-MM8
# ---------------------------------------------------------------------
echo "========== Commit 1: BL-MM7 + BL-MM8 =========="
git add edge/tool-bridge/src/catfish_tool_bridge/user_profile.py
git add edge/tool-bridge/src/catfish_tool_bridge/style_fingerprint.py
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
git add edge/tool-bridge/tests/test_user_profile.py
git add edge/tool-bridge/tests/test_style_fingerprint.py
git add edge/companion-app/src/tabs/Dashboard/UserProfileCard.tsx
git add edge/companion-app/src/tabs/Dashboard/StyleFingerprintCard.tsx
git add edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx
git add edge/identity/SOUL.md
git commit -F - <<'COMMIT_MSG_1'
BL-MM7+MM8: 用户画像 + 风格指纹完整链路 ship

BL-MM7 user_profile (4 工具):
- get/propose/confirm/clear, ALLOWED_FIELDS + NO_PROPOSE_FIELDS 红线
- 健康/财务/关系/政治/宗教不主动学
- 3 evidence 阈值, 3 次观察才主动问
- jsonl 落盘, 员工可一键 clear

BL-MM8 style_fingerprint (3 工具):
- get/refresh/clear, jieba 中文分词 + char-2gram fallback
- 时间衰减 (30/90/180 天 buckets)
- 写新文档前调 get_style_fingerprint 注入风格 anchor

Dashboard:
- UserProfileCard 显示员工自己被学到的画像 + 一键 clear
- StyleFingerprintCard 显示风格 token 频率

SOUL.md 加章节 'user_profile 怎么用' + 'style_fingerprint 是写文档前必备'

测试: tool-bridge 227 -> 253 (+26)
COMMIT_MSG_1

# ---------------------------------------------------------------------
# Commit 2 · 真主动 Phase A + B + 桌宠 polling
# ---------------------------------------------------------------------
echo
echo "========== Commit 2: 真主动 Phase A + B =========="
git add edge/companion-app/src/lib/triggers.ts
git add edge/companion-app/src/lib/triggers.test.ts
git add edge/companion-app/src/hooks/useProactiveTriggers.ts
git add edge/companion-app/src/hooks/useProactiveScheduler.ts
git add edge/companion-app/src/hooks/usePetStatusBroadcast.ts
git add edge/companion-app/src/tabs/Dashboard/ProactiveCard.tsx
git add edge/companion-app/pet.html
git add edge/companion-app/src/pet.tsx
git add edge/companion-app/src/App.tsx
git add edge/companion-app/src-tauri/src/commands/pet.rs
git add edge/companion-app/src-tauri/src/services/pet_hover.rs
git add edge/companion-app/src-tauri/src/services/mod.rs
git add edge/companion-app/src-tauri/src/lib.rs
git add edge/companion-app/src-tauri/tauri.conf.json
git add central/llm-gateway/src/catfish_gateway/proactive.py
git add central/llm-gateway/tests/test_proactive_contextual.py
git commit -F - <<'COMMIT_MSG_2'
Phase 2.5 真主动: 信号触发 + LLM context 化 + 桌宠 polling fix

Phase A 信号触发 (triggers.ts):
- detectSilence / detectDeadline / detectFocusReturn / shouldStaySilent
- 16 测试通过 (tsx 跑)

Phase B LLM context 化 (gateway/proactive.py):
- generate_contextual_starter(signal_kind, context)
- 3 SIGNAL_KIND_PROMPTS (silence/deadline/focus)
- 8 测试通过

桌宠跨 webview emit 修复:
- Tauri 2 emit/emitTo 不可靠, 改 Rust Mutex 缓冲 + 300ms tick polling
- pet_hover.rs 80ms 全局光标位置探测 (transparent click-through)
- 4 屏角 Option+Shift+1/2/3/4 快捷键妥协方案

useProactiveTriggers 1 分钟 tick:
- fetchContextualStarter 5s timeout, fallback 本地模板
- onFocusChanged 监听, 防 chat 中弹

测试: gateway 542 -> 550 (+8)
COMMIT_MSG_2

# ---------------------------------------------------------------------
# Commit 3 · G1-G7 安全 GAP 闭环
# ---------------------------------------------------------------------
echo
echo "========== Commit 3: G1-G7 安全 GAP =========="
git add central/llm-gateway/src/catfish_gateway/app.py
git add central/skills-hub/src/catfish_skills_hub/storage.py
git add central/skills-hub/src/catfish_skills_hub/app.py
git add central/skills-hub/tests/test_storage.py
git add .github/ 2>/dev/null || true
git add .security/ 2>/dev/null || true
git commit -F - <<'COMMIT_MSG_3'
G1-G7 安全 7 GAP 一气呵成 ship

G1 gateway HOST 默认 127.0.0.1 (强制本地, 不再 0.0.0.0 暴露)
G2 supply chain sha256 校验 + CATFISH_HUB_REQUIRE_HASH=1 严格模式
G3 execute_code 安全守卫 25 类正则 (5/7 BL-S29 加 OS 沙箱兜底)
G4 Tauri CSP null -> 白名单
G5 依赖 CVE 全 0 + CI 集成 (cargo audit / pip-audit / npm audit / gitleaks)
G6 数据流向图 (DATA-FLOW-DIAGRAM.md, commit 5 一并入库)
G7 secrets 扫源码 0 hit + CI gitleaks

测试: skills-hub 加 sha256 校验测试
COMMIT_MSG_3

# ---------------------------------------------------------------------
# Commit 4 · BL-S29 真技术沙箱 (12 天压 1 天)
# ---------------------------------------------------------------------
echo
echo "========== Commit 4: BL-S29 真技术沙箱 =========="
git add edge/tool-bridge/sandbox-profiles/
git add edge/tool-bridge/src/catfish_tool_bridge/sandbox.py
git add edge/tool-bridge/src/catfish_tool_bridge/adapter.py
git add edge/tool-bridge/tests/sandbox/
git add edge/tool-bridge/tests/test_sandbox_module.py
git add edge/tool-bridge/tests/test_e2e_sandbox.py
git commit -F - <<'COMMIT_MSG_4'
BL-S29 真技术沙箱 12 天 sprint 单日 ship - 86 测试全绿

BL-S29.1 macOS sandbox-exec profile (133 行 SBPL):
- 默认 allow + 5 类关键 deny (网络/写持久化/读敏感路径/iokit/sysctl)
- macOS 26 (Tahoe) 默认 deny 一切, 必须显式 (allow default)
- /etc symlink -> /private/etc 双等价 deny (8 高敏感文件)
- 25 恶意 + 6 sanity shell tests

BL-S29.2 sandbox.py 跨平台抽象 + adapter 拦截:
- detect_sandbox_kind() macOS/Linux/Docker 三层 fallback
- env CATFISH_SANDBOX_EXEC=1 拦 execute_code 走沙箱不去 hermes
- 沙箱内 env 干净 (剥 GITHUB_TOKEN), HOME 重定向到 TASK_DIR
- audit 加 sandbox_used / sandbox_kind 字段
- 18 unit tests

BL-S29.3 端到端 + demo 场景:
- 6 e2e tests (L1 字符串规则 + L2 沙箱 + audit + 兜底)
- MAY-DEMO-SCRIPT 场景 2.5 (30s 信安必演)

BL-S29.4 Linux nsjail (140 行 protobuf):
- clone_newnet/newpid/newns/newuser 真隔离
- rlimit_nproc=10 真拦 fork bomb (macOS 拦不住的关键)
- rlimit_as=512MB 真拦 mem bomb
- 25 恶意 + 6 sanity, docker run --privileged 验过

BL-S29.5 三层 fallback:
- sandbox-exec -> nsjail -> docker -> None 自动选
- _build_docker_args() --network=none + --read-only + --pids-limit=20 兜底

BL-S29.6 测试矩阵扩展 + 文档:
- macOS shell 25+6 / Linux nsjail 25+6 / python 18+6 = 86 测试
- 加 obfuscation (base64/eval/chr) / 跨进程 (ps/dscl/osascript) / 内核级
- SECURITY-REVIEW 附录 A: 56 case 矩阵 + 三层架构 + 央企信安 5 问预案

真发现 production bug:
- macOS /etc symlink 路径绕过 (双等价 deny)
- macOS 26 默认 deny (加 allow default)
- nsjail master 删 chroot 字段 (用 mount tmpfs)
COMMIT_MSG_4

# ---------------------------------------------------------------------
# Commit 5 · 客户文档矩阵 + Plan D 重定位
# ---------------------------------------------------------------------
echo
echo "========== Commit 5: 客户文档矩阵 =========="
git add README-FOR-CUSTOMERS.md
git add SECURITY-REVIEW-2026-05-06.md
git add DATA-FLOW-DIAGRAM.md
git add DEPLOYMENT-RUNBOOK.md
git add DEMO-CUSTOMER-QA-2026-05-14.md
git add DEMO-EMERGENCY-PLAN-2026-05-14.md
git add MAY-DEMO-SCRIPT-2026-05-14.md
git add QUICKSTART-EMPLOYEE.md
git add RUNBOOK-DEMO-VERIFICATION-5-14.md
git add docs/PLAN-D-PROTOCOL.md
git add docs/FEATURE-TRACKS.md
git commit -F - <<'COMMIT_MSG_5'
客户文档矩阵 + Plan D 重定位 (员工职业资产 / 跨雇主可携带)

Plan D 5/6 重定位 + 灵魂校准:
- peer-to-peer -> agent-as-service (公司谁愿答 X?)
- 鲶鱼 = 员工的'职业资产', 公司给员工配 (B2C2B)
- 数据所有权属员工本人, 跳槽带走 (cp ~/.catfish/)
- 8 文档统一刷: README + SECURITY § 1.5 + DATA-FLOW 边界 4 +
              DEPLOYMENT § 11 + DEMO-QA B 节 + B7 +
              PLAN-D § 11 + MAY-DEMO 场景 4.5 + FEATURE-TRACKS #8

5/14 demo 文档矩阵:
- README-FOR-CUSTOMERS (1 页快速了解)
- SECURITY-REVIEW-2026-05-06 (P0/P1/P2 + 附录 A 86 测试矩阵)
- DATA-FLOW-DIAGRAM (4 边界 + 出境 4 路径)
- DEPLOYMENT-RUNBOOK (3 部署架构 A/B/C)
- DEMO-CUSTOMER-QA (30 问预案)
- MAY-DEMO-SCRIPT (5 场景 + 场景 2.5 沙箱 + 场景 4.5 BL-FED2)
- QUICKSTART-EMPLOYEE / DEMO-EMERGENCY-PLAN / RUNBOOK-DEMO-VERIFICATION

FEATURE-TRACKS BL-S29 6 步全 ✅, Plan D #8 BL-FED2 路线图
COMMIT_MSG_5

# ---------------------------------------------------------------------
# Commit 6 · 杂项 fix + CHANGELOG
# ---------------------------------------------------------------------
echo
echo "========== Commit 6: 杂项 fix + CHANGELOG =========="
git add edge/companion-app/src-tauri/scripts/parse_file.py
git add edge/companion-app/src-tauri/src/commands/file_parse.rs
git add edge/companion-app/src-tauri/src/commands/relation.rs
git add edge/companion-app/src-tauri/tests/parse_file_test.py
git add edge/companion-app/src/lib/markdown.tsx
git add edge/companion-app/src/lib/chat.ts
git add edge/companion-app/src/lib/me.ts
git add edge/companion-app/src/lib/tauri.ts
git add edge/companion-app/src/store/ui.ts
git add edge/companion-app/package.json
git add edge/companion-app/package-lock.json
git add edge/companion-app/public/catfish-pet.svg
git add branding/pet-mascot.svg
git add central/llm-gateway/pyproject.toml
git add skills/department/leadership-briefing/SKILL.md
git add skills/department/project-approval/SKILL.md
git add skills/department/weekly-report/SKILL.md
git add CHANGELOG.md
git add COMMIT-PLAN-2026-05-07.md
git add commit-sprint.sh
git commit -F - <<'COMMIT_MSG_6'
杂项 fix + CHANGELOG 5/6+5/7 + sprint 同步脚本

- parse_file.py PDF 锚点表格检测 (320 人 ID 锚点结构化抽取)
- markdown.tsx 表格渲染修复 (rehype-raw + GFM 伪表重组)
- relation.rs / file_parse.rs / catfish-pet.svg 桌宠迭代
- skills/department 3 个 SKILL.md 微调
- CHANGELOG 5/6 + 5/7 完整 entry
- COMMIT-PLAN-2026-05-07.md + commit-sprint.sh (这次 sprint 自动化)
COMMIT_MSG_6

# ---------------------------------------------------------------------
echo
echo "========== 全部 commit 完毕 =========="
git log --oneline -8
echo
echo "如果都对, push:"
echo "    git push origin <你的分支>"
