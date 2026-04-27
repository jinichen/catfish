#!/usr/bin/env bash
# 把 2026-04-27 的工作 + 4-26 P1 收尾追登一并提交到 git.
#
# 拆 7 个 commit, 每个聚焦一件事:
#   1. 4-26 P1 收尾 (#43-#49: roleplay/POSITIONING/软技能/skin/Skill纪律/hot-reload + R6 R7)
#   2. email-agent MVP (#28 #33: Foxmail Mac SQLite + .mail 适配器)
#   3. Companion #50: Chrome cdp_url 自动刷新 + tool-bridge respawn
#   4. tool-bridge: catfish_screenshot + catfish_browser_goto + browser SKILL 修正
#   5. Companion: ChatInput 多模态附件 + 自动切视觉模型 (#52)
#   6. Gateway: vision fallback chain + connection error 关键词
#   7. SOUL/policy: 多模态自我认知 + 系统操作纪律 + memory 三层触发 + R8删 R9加
#   8. CHANGELOG: 2026-04-27 详细日志
#
# 使用:
#   bash scripts/commit-2026-04-27.sh           # 实际跑
#   bash scripts/commit-2026-04-27.sh --dry-run  # 只打印, 不动 git
#
# 完成后:
#   git log --oneline -10  # 查看 8 个新 commit
#   git push origin main   # 推到 github (或你的 main 分支名)

set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    echo "=== DRY RUN — 只显示 git 命令, 不实际执行 ==="
    echo
fi

cd "$(dirname "$0")/.."

run() {
    echo "→ $*"
    if [ "$DRY_RUN" = "0" ]; then
        eval "$@"
    fi
}

# 检查是不是在 catfish 项目根
if [ ! -f "CHANGELOG.md" ] || [ ! -d "central/llm-gateway" ]; then
    echo "✗ 当前目录不像 catfish 项目根 (找不到 CHANGELOG.md / central/llm-gateway)"
    exit 1
fi

# 检查是不是有未保存的 working tree (这个 script 假设你已经存盘)
if ! git diff --quiet --exit-code 2>/dev/null; then
    : # 有 modified, 正常
fi

# 检查 git index.lock 残留
if [ -f ".git/index.lock" ]; then
    echo "⚠ .git/index.lock 存在 — 可能上次 git 异常退出. 删掉:"
    run "rm .git/index.lock"
fi

echo "=== 先 unstage 所有改动, 让 script 自己按 commit 精准 git add ==="
echo "(防止跑 script 前你执行过 git add . 导致所有文件捏到第一个 commit 里)"
run "git reset HEAD >/dev/null 2>&1 || true"
echo

echo "=== 检查 working tree 状态 (应该全是 M / ?? 没 A) ==="
git status --short | head -40
echo

# ============================================================
# Commit 1 · 4-26 P1 收尾 (一并追登)
# ============================================================
echo "=== Commit 1/8 · 4-26 P1 收尾 (#43-#49 + R6 R7) ==="
run 'git add edge/communication-coach/'
run 'git add docs/POSITIONING.md'
run 'git add edge/companion-app/src-tauri/src/commands/learning.rs'
run 'git add edge/companion-app/src/types/learning.ts'
run 'git add edge/companion-app/src/tabs/Dashboard/LearningCard.tsx'
run 'git add edge/branding/catfish'
run 'git add edge/tool-bridge/src/catfish_tool_bridge/skill_watcher.py'
run 'git add edge/tool-bridge/tests/test_skill_watcher.py'

run "git commit -m 'feat(self-evolution): 4-26 P1 收尾 (#43-#49)' \
  -m '#43 catfish-roleplay: 3 阶段演练 SKILL (~310 行 SKILL.md), 收集 4 项→在角色里→系统反馈 (3✓+3✗+方法论命名+next-time)' \
  -m '#45 POSITIONING: docs/POSITIONING.md 14 章 ~430 行战略定位文档' \
  -m '#46 Self-Evolution 软技能扩: learning.rs +5 字段 (coaching_sessions today/this_week/prev_week, emails_drafted_today, methodologies_this_week); LearningCard 同步显示' \
  -m '#47 catfish wrapper skin 修: ensure_skin 默认 display.skin=default, 品牌 skin opt-in via CATFISH_BRAND_SKIN=1; nuke_proxy_for_hermes 防代理污染' \
  -m '#49 skill_watcher hot-reload: tool-bridge 后台 daemon poll ~/.hermes/skills/ 的 SKILL.md mtime, 变化后等 30s quiet period (没 dispatch_tool 在跑) → os._exit(0) → autostart 接管 respawn 加载新 skill. 13 单测.' "

# ============================================================
# Commit 2 · email-agent MVP
# ============================================================
echo
echo "=== Commit 2/8 · email-agent MVP for Foxmail Mac (#28 #33) ==="
run 'git add edge/email-agent/'

run "git commit -m 'feat(email-agent): MVP for Foxmail Mac (#28 #33)' \
  -m 'EmailAdapter ABC + frozen dataclasses (Account / Message / Attachment / ListFilter), 4 子类计划 (Foxmail Mac/Win + Outlook Mac/Win)' \
  -m 'foxmail_mac.py: Foxmail Mac 1.5+ SQLite + .mail 文件适配器. 直读 ~/Library/Containers/com.tencent.Foxmail/Data/Library/Foxmail/Profiles/<email>/messages.db + Mail/<folder_id>/<bucket>/<mailid>.mail. 不需要密码/IMAP/web. supports_drafts=False (raise NotSupportedError, 让 SKILL 降级到 quote 给员工粘贴)' \
  -m 'foxmail_db.py: SQLite 层封装 (FTS3 with ICU tokenizer fallback to LIKE)' \
  -m 'box_parser.py: 历史的 .box 格式 fallback (Foxmail 1.5 之前) + 32 单测覆盖' \
  -m 'CLI: catfish-email accounts/list/read/search 子命令, --human / --json 输出. SKILL.md (catfish-email) 触发词 + 决策表'"

# ============================================================
# Commit 3 · Companion Chrome cdp_url 自动刷新 (#50)
# ============================================================
echo
echo "=== Commit 3/8 · Companion Chrome cdp_url 自动刷新 (#50) ==="
run 'git add edge/companion-app/src-tauri/src/commands/chrome.rs'
run 'git add edge/companion-app/src-tauri/src/services/catfish_paths.rs'

run "git commit -m 'feat(companion): Chrome cdp_url 自动刷新 + tool-bridge respawn (#50)' \
  -m '痛点: 每次 Chrome 重启 webSocketDebuggerUrl UUID 变, 但 ~/.hermes/config.yaml 的 cdp_url 写死, 不刷新员工敲 browser_navigate 就 404. 之前要靠 catfish-browser-attach.sh 手动跑.' \
  -m 'chrome.rs chrome_launch 后 tokio::spawn 后台 poll Chrome /json/version (60 次 retry × 500ms = 30s 兜底), 拿到 webSocketDebuggerUrl 写入 yaml browser.cdp_url 字段 (yaml 解析 + 已是同值 skip 防 inotify 噪音)' \
  -m '同步刷 config 后 kill tool-bridge daemon (autostart 1-2s 接管 respawn 拉新 config) — 因为 tool-bridge 是常驻进程, hermes 模块 import 时把 cdp_url 缓存内存里, 光改 config 不够' \
  -m 'catfish_paths.rs 新增 hermes_config_path() 助手. 容错: Chrome 没起来→静默放弃+log warn; hermes config 不存在→skip'"

# ============================================================
# Commit 4 · tool-bridge native tools (catfish_screenshot + catfish_browser_goto)
# ============================================================
echo
echo "=== Commit 4/8 · tool-bridge: catfish_screenshot + catfish_browser_goto + browser SKILL 修正 (#51 #54 #55 #58 #62) ==="
run 'git add edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py'
run 'git add edge/tool-bridge/src/catfish_tool_bridge/server.py'
run 'git add edge/tool-bridge/src/catfish_tool_bridge/adapter.py'
run 'git add edge/tool-bridge/tests/test_screenshot.py'
run 'git add edge/tool-bridge/tests/test_catfish_tools.py'
run 'git add edge/tool-bridge/tests/test_adapter.py'
run 'git add edge/tool-bridge/hermes-skill/'
run 'git add edge/browser-agent/hermes-skill/catfish-browser-task/SKILL.md'
run 'git add edge/browser-agent/hermes-skill/catfish-browser-compliance/SKILL.md'

run "git commit -m 'feat(tool-bridge): catfish_screenshot + catfish_browser_goto native tools' \
  -m '#51 catfish_screenshot: mac screencapture / win PIL.ImageGrab. 默认 mode=fullscreen (0 权限 0 打扰), interactive/window 备选. 12MB 单张上限. server.py readline limit 拉到 16MB. SKILL + install.sh.' \
  -m '#54→#62 active_window 模式踩坑回退: v2 加了 mode=active_window 走 osascript tell System Events 拿前台窗口 ID, 但 macOS Automation 权限对 hermes venv 的 unsigned python3.11 不持久化, 反复弹对话框. v3 彻底删 active_window mode 和 _get_frontmost_window_id_macos() 函数, 默认改回 fullscreen.' \
  -m '#58 catfish_browser_goto: 走原生 CDP page-level Page.navigate, 绕开 hermes browser_navigate 在隔离 Chrome 上「调用 ✓ 但页面没真换」+ 模型编「已打开」的 hallucination. /json/list 拿 page-target → page-level WS → Page.navigate → Runtime.evaluate 拿真 title+url 反馈. 自适应 websocket-client/websockets 任一 lib.' \
  -m '#55 catfish-browser-task SKILL 修: 工具选择铁律表 — 验证码 / 像素级精细识别永远 catfish_screenshot, browser_vision 仅粗看. 历史教训 e3=xtF7 hallucination.' \
  -m '70 单测全过 (catfish_today_summary 24 + screenshot 19 含 deprecated active_window 拒绝 + browser_goto 7 + adapter 7 + skill_watcher 13)'"

# ============================================================
# Commit 5 · Companion ChatInput 多模态附件 (#52)
# ============================================================
echo
echo "=== Commit 5/8 · Companion ChatInput 多模态附件 + 自动切视觉模型 (#52) ==="
run 'git add edge/companion-app/src/types/chat.ts'
run 'git add edge/companion-app/src/lib/chat.ts'
run 'git add edge/companion-app/src/hooks/useChat.ts'
run 'git add edge/companion-app/src/tabs/Chat/ChatInput.tsx'
run 'git add edge/companion-app/src/tabs/Chat/ChatMessage.tsx'
run 'git add edge/companion-app/src/tabs/Chat/ChatPanel.tsx'
run 'git add edge/companion-app/src/tabs/Chat/ChatTab.tsx'

run "git commit -m 'feat(companion): ChatInput 多模态附件 + 自动切视觉模型 (#52)' \
  -m 'types/chat.ts: 新 Attachment interface (kind/mimeType/name/base64/sizeBytes) + ChatMessage.attachments? 字段' \
  -m 'lib/chat.ts: toWire() 检测 user message 含附件 → 输出 OpenAI multimodal content array [{type:text}, {type:image_url, image_url:{url:data:...}}]' \
  -m 'tabs/Chat/ChatInput.tsx: 完全重写, 📎 按钮 + onPaste 捕获剪贴板图片 + onDragOver/Drop 拖入区域(背景变浅青色反馈) + 缩略图行(64×64+×删除) + max 6张 + 12MB 单张校验' \
  -m 'hooks/useChat.ts: send() 改签名 (content, attachments?). 新增 maybeSwitchToVision: 当前模型 supports_vision=false 时按优先级 [private-vision, public-qwen-flash, public-gemini-flash/pro] 选第一个可用的, 切了在对话流追加 🔁 提示, 切不到短路返回不去硬发. runOneRound streamChat({model: useChatStore.getState().model}) 从 store snapshot 读避免 closure 旧 model.' \
  -m 'tabs/Chat/ChatMessage.tsx: UserBubble 渲染图片缩略图(max 220x220 contain). ChatPanel/ChatTab: handleSend(text, attachments) 透传.' \
  -m 'State.db 持久化: 图片 base64 不入库, 文字+占位符 [📎 N 张图片 — Companion in-memory, 切会话不保留]. tsc --noEmit 通过.'"

# ============================================================
# Commit 6 · Gateway vision fallback + connection error
# ============================================================
echo
echo "=== Commit 6/8 · Gateway vision fallback chain + connection error 关键词 ==="
run 'git add central/llm-gateway/config/models.yaml'
run 'git add central/llm-gateway/src/catfish_gateway/fallback.py'

run "git commit -m 'fix(gateway): catfish-private-vision 加 fallback chain + connection error 进 on_errors' \
  -m '4-26 P1 fallback 链覆盖了 main + gemini-pro/flash, 但漏了 vision. 内网 10.10.40.x VPN 抖动时 vision 直接挂员工.' \
  -m 'models.yaml: catfish-private-vision 加 fallback {chain:[catfish-public-qwen-flash, catfish-public-gemini-flash], on_errors:[429, 502, 503, 504, timeout, connection error, connection refused]}. 所有现有 fallback on_errors 也补齐这两个关键词.' \
  -m 'fallback.py _ERROR_KEYWORDS 加同义词扩展 (connection error → cannot connect/connect call failed/broken pipe/apiconnectionerror) — 实际撞内网 wifi 抖动时 LiteLLM 抛的就是这一组. 24 现有测试不回归 + 4 新 keyword 验证.'"

# ============================================================
# Commit 7 · SOUL/policy 反幻觉三件套
# ============================================================
echo
echo "=== Commit 7/8 · SOUL + catfish-policy: 反幻觉 + 系统操作 + memory 三层触发 ==="
run 'git add edge/identity/SOUL.md'
run 'git add plugins/catfish-policy/rules.yaml'

run "git commit -m 'docs(soul,policy): 反幻觉 + 系统操作 + memory 三层触发 + R8删R9加' \
  -m '#53 SOUL 多模态自我认知段: 防 Qwen3-VL 在 browser-task 上下文里 vision tool 调失败后错位成「我没视觉能力」. 列出当前多模态模型 + ✅ image_url 直接看 + ❌ 三句严禁说.' \
  -m '#56 SOUL 系统操作不让员工跑 shell 段: 模型遇到 cdp 失效给员工列 6 步手动命令 — 改成优先级表 (catfish 内置 tool / Companion 按钮 / shell≤2条). 标准答案表覆盖 cdp/Companion/tool-bridge/gateway/skill 5 类常见症状.' \
  -m '#59 SOUL 工具调用失败时不要幻觉级联段: 模型撞 tool 不可用→编 config 坏→改 ~/.hermes/config.yaml. 4 步自问 + 4 条永远不做.' \
  -m '#60-#61 SOUL Memory 写入纪律强化: 三层触发器 (员工原话 / 反复纠正≥2次 / 长期模式≥5次稳定信号). 自检 4 步. 风格事实 vs 性格 narrate 对比表 (写「鸿波偏好表格输出」不写「鸿波是结构化思考者」). memory 生命周期影响说明.' \
  -m 'catfish-policy R8 删 (active_window mode 已从代码删, deny rule 不再需要; 编号保留作为踩坑标记).' \
  -m 'catfish-policy R9 加 no-touch-hermes-control-files: 禁止 LLM patch/write_file 改 ~/.hermes/{config.yaml, SOUL.md, USER.md}. 历史 LLM 调不存在 memory tool→编 config 坏→自作主张改 yaml 加 provider:auto 防御.'"

# ============================================================
# Commit 8 · CHANGELOG
# ============================================================
echo
echo "=== Commit 8/8 · 4-27 docs + Skill lifecycle 防御 + commit script ==="
run 'git add CHANGELOG.md'
run 'git add docs/BACKLOG.md'
run 'git add docs/COMPETITIVE-DIFFERENTIATION.md'
run 'git add docs/POSITIONING.md'
run 'git add docs/SKILL-LIFECYCLE.md'
run 'git add scripts/commit-2026-04-27.sh'
run "git commit -m 'docs+feat: 4-27 收官 (CHANGELOG/BACKLOG/COMPETITIVE-DIFF/SKILL-LIFECYCLE)' \
  -m 'CHANGELOG: 4-27 详细日志 (视觉/多模态全栈打通 + SOUL 反幻觉补丁 + 4-26 P1 收尾追登 — 8 项完成 + 7 条踩坑)' \
  -m 'docs/BACKLOG.md (新增): 全量任务积压 121+ 项 / 11 个 section (战略/GTM/工程 P1-P3/运营/法律/多模态/品牌/Skill lifecycle). Task tracker 装 sprint, BACKLOG 装全量, 每周 review' \
  -m 'docs/COMPETITIVE-DIFFERENTIATION.md (新增): 应对「鲶鱼跟 Hermes/OpenClaw 同质化」质疑. 三层架构图 (Linux/Ubuntu 类比) + 16 维硬指标对比 + 4 种误读反驳 + 5 条 hard diff + 销售 4 件套话术 + Demo 三连镜头. 销售/pitch/招聘统一引用源' \
  -m 'POSITIONING.md 加固: § 11 FAQ 加「跟 Hermes 同质化」条目; § 12 招牌镜头改成 0 配置启动 / 接 Foxmail / 演练方法论 三连; § 14 加锁层差' \
  -m 'docs/SKILL-LIFECYCLE.md (新增): Skill 5 阶段框架 (Plan/Create/Review/Use/Evolve), 元文档指导 SOUL/policy/tool 设计. 当前覆盖 Plan + Create + Evolve(部分), 其余进 BACKLOG P1' \
  -m 'scripts/commit-2026-04-27.sh: 本次 8 个 commit 的执行脚本 (git reset HEAD 自我幂等)'"

# ============================================================
# 完成
# ============================================================
echo
echo "=== 全部 8 个 commit 完成 ==="
git log --oneline -10
echo
echo "下一步:"
echo "  git push origin main      # 推到 github (替换成你的 main 分支名, 例如 main / master / develop)"
echo "  git log --stat -8         # 看每个 commit 的 file 变化"
