#!/usr/bin/env bash
# 一键拆 6 个 commit 提交今天 (2026-04-26) 的工作
#
# 安全设计:
#   - 跑之前先 git status, 让你确认没漏文件
#   - 每个 commit 前 echo 出主题, 出错立刻停 (set -e)
#   - 只 commit 不 push, 最后让你 git log 复检自己 push
#   - 跑完不删脚本本身, 想重跑可以 git reset --soft HEAD~6 后再来
#
# 用法:
#   bash scripts/commit-2026-04-26.sh
#
# 异常时:
#   git status            看现状
#   git log --oneline -8  看最近 commit
#   git reset --soft HEAD~N  撤回 N 个 commit (保留改动)

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."  # 跑到 catfish/ 根

# ---------- 颜色 ----------
if [ -t 1 ]; then
    BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=''; CYAN=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi

step() { echo "${BOLD}${CYAN}▶ $*${RESET}"; }
ok()   { echo "${GREEN}✓${RESET} $*"; }
warn() { echo "${YELLOW}⚠${RESET} $*"; }
err()  { echo "${RED}✗${RESET} $*"; exit 1; }

# ---------- 预检 ----------
step "预检 1/3: 在 git 仓库里"
git rev-parse --is-inside-work-tree >/dev/null || err "不在 git 仓库里"

step "预检 2/3: 工作树状态"
git status --short

step "预检 3/3: .env 不应该被跟踪"
if git ls-files --error-unmatch central/llm-gateway/.env >/dev/null 2>&1; then
    err ".env 被 git 跟踪了! 跑 'git rm --cached central/llm-gateway/.env' 先"
fi
ok ".env 已 gitignore"

echo
echo "${BOLD}=== 看完上面 git status, 确认要继续 commit? (y/N) ===${RESET}"
read -r -n 1 ans
echo
# Bash 3.2 (macOS 默认) 不支持 ${var,,}, 用 case 兼容
case "$ans" in
    y|Y) ;;
    *) err "用户取消" ;;
esac

# ============================================================
# Commit 1: env-var 化
# ============================================================
step "Commit 1/6: refactor(env): 全栈硬编码 → env vars"

git add \
    .env.example \
    edge/companion-app/.gitignore \
    central/llm-gateway/.env.example \
    central/llm-gateway/config/models.yaml \
    central/llm-gateway/src/catfish_gateway/config.py \
    central/llm-gateway/src/catfish_gateway/network.py \
    central/llm-gateway/docker-compose.yml \
    central/llm-gateway/README.md \
    central/llm-gateway/tests/test_config_interpolate.py \
    edge/companion-app/src-tauri/src/services/endpoints.rs \
    edge/companion-app/src-tauri/src/services/mod.rs \
    edge/companion-app/src-tauri/src/commands/health.rs \
    edge/companion-app/src-tauri/src/commands/gateway.rs \
    edge/companion-app/src-tauri/src/commands/chrome.rs \
    edge/companion-app/src/lib/env.ts \
    edge/companion-app/src/lib/http.ts \
    edge/feishu-monitor/src/catfish_feishu/config.py \
    edge/branding/catfish \
    edge/browser-agent/scripts/catfish-browser-attach.sh \
    plugins/catfish-policy/rules.yaml \
    docs/operations/troubleshooting.md

git commit -m 'refactor(env): extract hardcoded IPs/ports/hostnames to env vars

P0-2: 全栈把 10.10.40.x / :8999 / :9222 等硬编码搬到 env vars,
让仓库可以 open-source 而不泄漏内网拓扑.

Gateway:
- config.py 加 ${VAR} / ${VAR:-default} 插值机制
- models.yaml 三个 api_base 改占位符 INTERNAL_LLM_BASE_*
- docker-compose 默认端口 8000 -> 8999 全栈对齐

Companion (Rust):
- 新增 services/endpoints.rs (OnceLock<Endpoints>) 集中读端口
- health/gateway/chrome 三个命令全用 endpoints, 字面值清零

Companion (TS):
- env.ts 走 VITE_CATFISH_GATEWAY_URL > host+port > 默认
- http.ts 默认 base 不再硬编码

Bash + 文档同步: catfish wrapper / browser-attach.sh / policy 提示文案 /
troubleshooting.md / README.md 内的 IP 字面值全部去掉.

测试: 18 个 _interpolate_env 单测全过.'

ok "Commit 1 done"

# ============================================================
# Commit 2: P0-1 catfish_today_summary
# ============================================================
step "Commit 2/6: feat(self-evolution): catfish_today_summary"

git add \
    edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py \
    edge/tool-bridge/src/catfish_tool_bridge/adapter.py \
    edge/tool-bridge/src/catfish_tool_bridge/server.py \
    edge/tool-bridge/tests/

git commit -m 'feat(self-evolution): catfish_today_summary native tool

P0-1: 修员工问 "今天学了什么" 时 LLM 调 session_search 答非所问的 UX bug.
给 LLM 一个明确的工具回答今日活动 (会话数 / 工具调用 / token / memory / skill).

- 新增 catfish_tools.py (210 行): CATFISH_NATIVE_TOOLS schema + dispatch
  逻辑直读 ~/.hermes USER.md / memories / skills / state.db, 跟 Tauri 端
  learning.rs 同一份逻辑的 Python 镜像 (不走 IPC, 因为 tool-bridge 起来时
  Companion 不一定开着)
- adapter.py: list_tools 把 native 排前面, dispatch_tool 不走 hermes
  registry 直接分发
- server.py 启动 banner 区分 hermes 60 + catfish 原生 N

测试: 23 个 (catfish_tools 16 + adapter 7), 0.05s 全过.'

ok "Commit 2 done"

# ============================================================
# Commit 3: Gateway hardening
# ============================================================
step "Commit 3/6: feat(gateway): Gemini guard + sanitizer + fallback + Qwen"

git add \
    central/llm-gateway/src/catfish_gateway/gemini_guard.py \
    central/llm-gateway/src/catfish_gateway/tools_sanitizer.py \
    central/llm-gateway/src/catfish_gateway/fallback.py \
    central/llm-gateway/src/catfish_gateway/app.py \
    central/llm-gateway/src/catfish_gateway/config.py \
    central/llm-gateway/config/models.yaml \
    central/llm-gateway/.env.example \
    central/llm-gateway/tests/test_catalog.py \
    central/llm-gateway/tests/test_gemini_guard.py \
    central/llm-gateway/tests/test_tools_sanitizer.py \
    central/llm-gateway/tests/test_fallback.py

git commit -m 'feat(gateway): Gemini guard + tools sanitizer + fallback chain

三层网关防御 + Qwen3.6-Flash 接入.

P0-4 Gemini guard (gemini_guard.py):
  Gemini 2.x/3.x 在 tools=[] 时输出 native tool_code Python 伪代码,
  LiteLLM 包成假 tool_call 把对话搞乱. 在 system message 末尾 append
  禁止 tool_code 的中英双语指令, 幂等. 18 单测.

Fix tools sanitizer (tools_sanitizer.py):
  Companion 早期版本误把 ToolInfo.input_schema 当 OpenAI tool.function 用,
  缺 name 字段, Qwen 宽容能跑, Gemini 直接 KeyError 500. 网关侧防御性丢弃
  畸形 tool. 14 单测.

P1 模型 fallback 链 (fallback.py):
  models.yaml 加 fallback: {on_errors, chain, max_hops}. 上游 429/503/504/
  timeout 时按 chain 自动重试. Streaming 在拉首 chunk 阶段 fallback,
  已开始流的中途错误不切. Gemini 模型预设 fallback 到 qwen-flash -> private.
  24 单测.

错误返回人话化 (_friendly_upstream_error):
  429/quota → "免费配额今日耗尽", 401 → "API Key 无效" 等, 不再吐 trace.

Qwen3.6-Flash 接入 (models.yaml + .env.example):
  阿里云百炼 OpenAI 兼容端点, 256K 上下文 + 多模态. DASHSCOPE_API_KEY 走 env.
  零代码改动接新模型证明 P0-2 配置驱动到位.

Gateway 累计 118 个 Python 单测全过.'

ok "Commit 3 done"

# ============================================================
# Commit 4: Plan C Week 3 + Companion UX
# ============================================================
step "Commit 4/6: feat(companion): sidebar + resume + autostart + icon"

git add \
    edge/companion-app/src-tauri/src/services/autostart.rs \
    edge/companion-app/src-tauri/src/services/mod.rs \
    edge/companion-app/src-tauri/src/lib.rs \
    edge/companion-app/src-tauri/src/commands/sessions.rs \
    edge/companion-app/src-tauri/src/commands/session_write.rs \
    edge/companion-app/src-tauri/src/commands/learning.rs \
    edge/companion-app/src-tauri/Cargo.toml \
    edge/companion-app/src-tauri/Cargo.lock \
    edge/companion-app/package-lock.json \
    edge/companion-app/src/types/session.ts \
    edge/companion-app/src/store/chat.ts \
    edge/companion-app/src/store/ui.ts \
    edge/companion-app/src/components/TabBar.tsx \
    edge/companion-app/src/App.tsx \
    edge/companion-app/src/hooks/useChat.ts \
    edge/companion-app/src/tabs/Chat/ChatTab.tsx \
    edge/companion-app/src/tabs/Chat/ChatSidebar.tsx \
    edge/companion-app/src/tabs/Dashboard/ServicesCard.tsx \
    edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx \
    edge/companion-app/package.json \
    edge/companion-app/src-tauri/icons/

git commit -m 'feat(companion): sidebar + resume + autostart + squircle icon

P0-3.1 + 3.2 多会话 sidebar:
  对话 tab 重做 [左侧 SessionList | 右侧 ChatPanel] 布局.
  ChatStore.loadSession 把 DB 历史 messages 灌进 store 做 resume.
  Rust sessions.rs 扩 SessionDetail.messages 返回全消息 (timestamp asc).
  "会话" top tab 砍掉 (跟 sidebar 重复), "+ 新会话(终端开 hermes)"
  挪到 sidebar 底部.

P0-5 Companion 自起 tool-bridge:
  services/autostart.rs 在 Tauri setup hook 后台 spawn gateway + tool-bridge,
  已在跑 no-op. 解决 P0-4 根因 (没工具列表 -> Gemini 退化).

Fix#21 useChat tool schema:
  ensureTools() 错把 ToolInfo.input_schema 当整个 function, 改成正确的
  {name, description, parameters} 装配.

Dashboard 加服务概览卡 (ServicesCard.tsx):
  4 行紧凑显示 gateway/tool-bridge/chrome/local-search 状态 + 跳控制台链接.

App 图标 macOS squircle 化:
  原 icon 圆形填满 1024 (无 padding) 在 dock 比 Chrome 大 25%, 改为
  superellipse n=5 squircle (Apple HIG) + 鲶鱼填 95%.

Rust 单测 27 个 (endpoints 6 + session_write 7 + learning 14) 待本机
cargo test 跑.'

ok "Commit 4 done"

# ============================================================
# Commit 5: catfish wrapper proxy + skill + docs
# ============================================================
step "Commit 5/6: fix(catfish): proxy hygiene + compliance skill + docs"

git add \
    edge/branding/catfish \
    edge/browser-agent/hermes-skill/catfish-browser-compliance/ \
    edge/browser-agent/hermes-skill/install.sh \
    central/llm-gateway/README.md \
    docs/operations/troubleshooting.md

git commit -m 'fix(catfish): proxy hygiene + browser-compliance skill + docs

Fix catfish wrapper 净化代理 (#31):
  hermes httpx client 不读 NO_PROXY, 员工 shell 设了 HTTPS_PROXY=:7890
  时调 localhost:8999 网关被代理劫持. catfish wrapper 加 nuke_proxy_for_hermes
  在 exec hermes 前 unset HTTPS_PROXY/HTTP_PROXY/ALL_PROXY,
  CATFISH_KEEP_PROXY=1 保留逃生口. Companion 不受影响 (gateway 内部独立处理).

P1-2 catfish-browser-compliance skill:
  把 4-24 内网管理系统通路抽象成 hermes skill (~270 行 SKILL.md).
  4 大模式 (列+筛选 / 详情 / 触发分析 / 下载报告) 沉淀, "填表不自提交"
  红线, 失败降级 (DOM 失效 -> URL fallback), 输出格式约定.
  install.sh 同时装 task + compliance 两个 skill.

文档:
  README.md 新增 "⚠ 用 catfish 命令, 不要裸跑 hermes" 段.
  troubleshooting.md 1.2 节改写, 推 catfish wrapper 为首选方案.'

ok "Commit 5 done"

# ============================================================
# Commit 6: CHANGELOG
# ============================================================
step "Commit 6/6: docs(changelog): 2026-04-26"

git add CHANGELOG.md docs/TOMORROW.md scripts/commit-2026-04-26.sh

git commit -m 'docs(changelog): 2026-04-26 详细日志

15 项推进:
- P0-1 ~ P0-5 全清 (catfish_today_summary / 硬编码清零 / sidebar+resume /
  Gemini guard / autostart tool-bridge)
- P1 fallback 链 + Qwen3.6-Flash 接入 + 错误人话化
- Fix useChat tool schema (致 Gemini KeyError 的根因)
- Fix catfish wrapper 代理净化
- UX 砍重复 "会话" tab + 加 Dashboard 服务概览卡 + macOS squircle 图标
- P1-2 browser-compliance skill 沉淀 4-24 内网通路

测试: gateway 118 + tool-bridge 23 = 141 Python 单测全过, 27 Rust 单测待跑.'

ok "Commit 6 done"

# ============================================================
# 收尾
# ============================================================
echo
echo "${BOLD}${GREEN}═══ 6 个 commit 全部完成 ═══${RESET}"
echo
echo "${BOLD}最后看一眼 git log:${RESET}"
git log --oneline -7
echo
echo "${BOLD}然后 push:${RESET}"
echo "    git push origin main"
echo
echo "${BOLD}如果想撤回所有 commit (保留代码改动):${RESET}"
echo "    git reset --soft HEAD~6"
echo
warn "脚本不自动 push, 你 review log 后手动 push."
