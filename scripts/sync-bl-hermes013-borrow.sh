#!/usr/bin/env bash
# BL-HERMES013-borrow (5/11) — 借鉴 Hermes 0.13 三件 (不升级 hermes).
#
# 鸿波 5/11 决策: demo 前不升 hermes 0.13 (4 天新 release, 风险高), 但抄 3 个
# 关键设计自实现:
#
#   1. audit/prompt_security scrub_credentials (error log 漏密码兜底)
#   2. Playwright cloud-metadata SSRF deny (防 IAM 泄露)
#   3. /goal Ralph loop (锁定目标多轮不偏, 跟 L7/L8/FIX46 互补)

set -e
cd "$(dirname "$0")/.."

if [ -f .git/index.lock ]; then
    rm -f .git/index.lock
fi

git fetch origin main 2>&1 | tail -3 || echo "(fetch 失败, 继续)"

# 1: scrub credentials
git add central/llm-gateway/src/catfish_gateway/prompt_security.py
git add central/llm-gateway/src/catfish_gateway/metrics.py
# 2: SSRF deny
git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py
# 3: /goal
git add central/llm-gateway/src/catfish_gateway/session_goals.py
git add central/llm-gateway/src/catfish_gateway/app.py
git add edge/identity/SOUL.md
# sync script self
git add scripts/sync-bl-hermes013-borrow.sh

git commit -m "BL-HERMES013-borrow (5/11): 借鉴 Hermes 0.13 三件 (不升级 hermes)

鸿波 5/11 决策: demo 前不升 hermes 0.13 (4 天前新 release, bug 暴露窗口),
但借鉴 3 个关键设计自实现, demo 后再做 0.13 升级 sprint.

# 1: BL-HERMES013-1 audit scrub credentials

借鉴 Hermes 0.13 'Default-on secret redaction'. catfish audit 从 day 1
已经永不写 prompt 内容 (metrics.py 文档强调), 但 error trace 字段
'error[:200]' 可能含 upstream LLM 回显的 prompt 片段 — 加 scrub 在
truncate 前一道兜底:

  - prompt_security.py: 新加 scrub_credentials_in_text(text) → 把
    password=xxx / 密码: xxx 等模式 sub 成 '[REDACTED:credential]'
  - metrics.py: log_request_metadata 写 error 字段前 scrub

# 2: BL-HERMES013-2 Playwright cloud-metadata SSRF deny

借鉴 Hermes 0.13 'Browser — enforce cloud-metadata SSRF floor in
hybrid routing'. 防 LLM 被 prompt injection 引导访问云厂商 metadata
端点 (AWS IMDS / GCP / Azure / 阿里云) 泄露 IAM credentials.

catfish_tools.py 加 _check_ssrf_safe(url):
  - deny: 169.254.169.254 / metadata.google.internal / 100.100.100.200 /
          169.254.170.2 / fd00:ec2::254 (云 metadata 端点)
  - deny: 169.254.0.0/16 (IPv4 link-local) + fe80::/10 (IPv6 link-local)
  - **allow** 内网 10.x / 192.168 / 172.16 / 127.0.0.1 (catfish 主客户
    央企内网正常 IP, 误伤会破坏 EIS / OA 等内系统访问)

_browser_goto_impl 早期 check, deny 时返 friendly error.
单测: 7 deny + 8 allow + 4 边界全过.

# 3: BL-HERMES013-3 /goal Ralph loop

借鉴 Hermes 0.13 '/goal — agent doesn't forget what you asked it to
do, locked onto a target across turns'.

跟 catfish 之前 turn 控制 fix 互补:
  - L7/L8: 事后纠偏 (LLM 中途 stop → retry)
  - FIX46: 事后纠偏 (LLM 自作主张 → SOUL 软纪律阻止)
  - **/goal: 事前锚定** (锁定目标多轮不偏)

新模块 session_goals.py (~200 行):
  - GOAL_PATH = ~/.catfish/session_goal.txt (单文件存储)
  - MAX_GOAL_LEN = 500 防 prompt injection 塞超长 fake goal
  - read_session_goal / write_session_goal / clear_session_goal
  - detect_goal_command(messages) → 检测最后一条 user 是不是 /goal 命令
  - inject_session_goal(messages) → 在 last system message 末尾追加 goal

App.py 接入两点:
  1. chat_completions 早期检测 /goal 命令 (在 LLM 调用前), 不调 LLM /
     不计 quota, 直接返 fake SSE response (_fake_sse_response 新 helper).
  2. inject pipeline 加 inject_session_goal (在 inject_feedback 之后),
     LLM 每轮看到 '当前锁定目标' system 段.

支持的命令:
  /goal <描述>     设新 goal
  /goal           显示当前 goal
  /goal clear     清除 (英文)
  /goal 清除       清除 (中文)
  /goal off       清除 (短)
  /goal 关闭       清除 (中文短)

SOUL.md 加 '★ /goal 锁定目标铁律' 段:
  - 看到 '当前锁定目标' = 把它当北极星
  - 临时偏题 OK 但完成后主动拉回
  - 完成 goal 后主动报告 + 提示员工 clear
  - 不能自己设 goal (员工 explicit 设)
  - 跟 '做完才说'/'做完不再问' 配合

# 不升 hermes 的理由

  - 0.13 release 5/7, 距今 4 天, 早期 bug 暴露窗口
  - 5/14 demo 前 freeze 高风险变更
  - catfish-policy plugin 4 条 matcher 跟 0.13 新 plugin surface 兼容性需测
  - default-on secret redaction × catfish audit 脱敏可能撞
  - 自己实现 90% 设计灵感, 真升级时再补 10% (multi-agent kanban / checkpoints v2)

# 升级 sprint 排期

  5/18-5/22 一周, 跟 catfish-web 5/15+ 中央门户并行做.
  详 BL-HERMES-UPGRADE-013 (task #) demo 后开.

# 12 单测全过

  - session_goals 读/写/清除/detect 4 命令/inject 幂等 (10 tests)
  - scrub_credentials 替密码模式 (1 test)
  - SSRF deny 7 + allow 8 + 边界 4 (catfish_tools.py 内单独验证)

# 部署

gateway 重启读新代码. tool-bridge 重启 (Companion 自动带起来). 不动 PG.
"

if [ -n "$(git status --porcelain)" ]; then
    echo "⚠ 还有 untracked / unstaged 改动."
fi

echo
git log origin/main..HEAD --oneline
echo
read -p "确认 push? [y/N] " yn
case $yn in
    [Yy]*) git push origin main && {
        echo "✅ done."
        echo
        echo "重启 gateway + Companion 后:"
        echo "  /goal 帮我看下今天的待办  → gateway 拦截 → 'goal 已锁定'"
        echo "  下次 chat → 每轮 system 看到 '🎯 当前锁定目标'"
        echo "  /goal clear → 清除"
    } ;;
    *) echo "❎ 取消." ;;
esac
