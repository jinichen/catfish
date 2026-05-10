#!/usr/bin/env bash
# BL-VOICE2/3 + BL-Q3-FACT 全段 sync 脚本 (5/10 夜).
#
# 鸿波 5/10 截图: Source Control 25 changes 没 commit, Cursor 转圈卡住.
# 真因: BL-ARCH1+ARCH2 + BL-SEC2 已经 push (commit 1308754 / 20e8d33),
# 但今晚后续一波 (BL-VOICE2 全套 + BL-VOICE3 + BL-Q3-FACT 设计) 一行没 commit.
#
# 这个脚本分 3 个 commit, 不再像之前 sync-bl-arch.sh 让 25 个文件混一坨:
#   1. BL-VOICE2/3: Piper TTS + 拖音频转文字 (Companion 全套改动)
#   2. BL-ARCH2 fix3~6: webUrl/sysadmin/LOGO/连通性等修
#   3. BL-Q3-FACT 设计文档 + BACKLOG/scripts/.gitignore

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)
echo "→ catfish 根目录: $ROOT"

# 1. 清残留 lock
if [ -f .git/index.lock ]; then
    echo "→ 清 .git/index.lock"
    rm -f .git/index.lock
fi

# 2. branch 确认
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
    echo "⚠ 当前 branch=$BRANCH, 不是 main. 中止." >&2
    exit 1
fi

# 3. 远程同步性确认 (避免覆盖别的同事 push)
git fetch origin main 2>&1 | tail -3
BEHIND=$(git rev-list --count HEAD..origin/main)
if [ "$BEHIND" -gt 0 ]; then
    echo "⚠ 本地落后远程 $BEHIND 个 commit. 先 git pull --rebase origin main 再跑这脚本"
    exit 1
fi
echo "✓ 本地跟远程同步 (BL-SEC2 / BL-ARCH1 已 push 过)"

# ════════════════════════════════════════
# Commit #1: BL-VOICE2 P0 + fix1~6 + BL-VOICE3 (Companion TTS+STT 完整一波)
# ════════════════════════════════════════
echo
echo "════════════════════════════════════════"
echo "Commit #1: BL-VOICE2 + BL-VOICE3 全套"
echo "════════════════════════════════════════"

# Rust 端 (TTS subprocess + 音频转录 + endpoints web_url 字段)
git add edge/companion-app/src-tauri/Cargo.lock
git add edge/companion-app/src-tauri/Cargo.toml
git add edge/companion-app/src-tauri/src/commands/mod.rs
git add edge/companion-app/src-tauri/src/commands/tts.rs              # NEW BL-VOICE2
git add edge/companion-app/src-tauri/src/commands/speech.rs           # BL-VOICE3 加 transcribe_audio
git add edge/companion-app/src-tauri/src/commands/endpoints.rs        # ARCH2 fix2/3 web_url
git add edge/companion-app/src-tauri/src/services/endpoints.rs        # 同上
git add edge/companion-app/src-tauri/src/lib.rs                       # 注册 tts/transcribe
git add edge/companion-app/src-tauri/tauri.conf.json                  # CSP media-src + assetProtocol scope

# 前端 (TTS 喇叭 / lib helper / Dashboard 链接 / Chat 拖音频)
git add edge/companion-app/src/lib/tts.ts                             # NEW
git add edge/companion-app/src/components/TTSButton.tsx               # NEW
git add edge/companion-app/src/lib/markdown.tsx                       # ARCH2 fix1 shell.open
git add edge/companion-app/src/lib/env.ts                             # ARCH2 fix2/3 webUrl
git add edge/companion-app/src/lib/me.ts                              # ARCH1 P1 sysadmin role
git add edge/companion-app/src/lib/chat.ts                            # VOICE3 audio attachment
git add edge/companion-app/src/tabs/Chat/ChatInput.tsx                # VOICE3 拖音频
git add edge/companion-app/src/tabs/Chat/ChatMessage.tsx              # VOICE2 喇叭按钮
git add edge/companion-app/src/tabs/Dashboard/DashboardTab.tsx        # ARCH2 瘦身 (之前没 commit?)
git add edge/companion-app/src/tabs/Dashboard/WebPortalLink.tsx       # NEW ARCH2

# 文档 + 一键安装脚本
git add edge/companion-app/docs/PIPER-TTS-SETUP.md                    # NEW
git add edge/companion-app/scripts/install-piper-tts.sh               # NEW

git commit -m "BL-VOICE2 + BL-VOICE3 (5/10): Piper local TTS + 拖音频转文字 + ARCH2 fix1~6

跟 whisper.cpp STT 对称: 100% 本地, 数据不出公司, 央企场景适配.
鸿波 5/10 夜 'a16z piper TTS 这么好玩没理由不现在做' 触发.

──────────── BL-VOICE2 P0+fix1~6: Piper local TTS ────────────

新文件:
- src-tauri/src/commands/tts.rs (~310 行): tts_synthesize / tts_status,
  piper subprocess + 模型探测 + yaml.tts.voice 持久配置 + size 校验防 LFS pointer
- src/lib/tts.ts (~280 行): 前端 invoke + Audio 播放, 全局唯一 audio + token
  防 race, stripMarkdownForTTS (14 条规则) + insertCommasInLongRuns 长句插逗号
- src/components/TTSButton.tsx (~115 行): 🔊 按钮组件, 4 态 (idle/loading/playing/error)
- docs/PIPER-TTS-SETUP.md: 部署文档 (踩坑过程 + venv 真路子 + 故障排查)
- scripts/install-piper-tts.sh: 一键装脚本 (绕 PEP 668 + 7890 代理 + LFS pointer 三连坑)

集成改动:
- src-tauri/src/commands/mod.rs: + pub mod tts
- src-tauri/src/lib.rs: 注册 tts_synthesize / tts_status
- src-tauri/tauri.conf.json: CSP + media-src + assetProtocol scope (/tmp/catfish-tts-*.wav)
- src-tauri/Cargo.lock + Cargo.toml: 加 base64 / serde_yaml 依赖
- src/tabs/Chat/ChatMessage.tsx: AssistantBubble 加 TTSButton
- src/lib/markdown.tsx: 链接 onClick shell.open (Tauri webview 修)

fix1: Tauri webview 默认吞 <a target=_blank> → onClick + shell.open
fix2: webUrl 硬编码 + sessionStorage 关 tab 丢 + AuthCallback 白屏
fix3: webUrl prod fallback 误推 gateway origin → 全部 404
fix4: catfish-web 没起友好提示 + 心跳检测 + vite 0.0.0.0 strictPort
fix5: voice 模型 size 校验防 HuggingFace LFS pointer 假文件
fix6: 长句插逗号 (regex + 助词识别, 不依赖 jieba)

──────────── BL-VOICE3: 拖音频转文字 attachment ────────────

新文件 (无, 复用 speech.rs whisper 基础设施):

集成改动:
- src-tauri/src/commands/speech.rs: + transcribe_audio_from_b64 (~120 行)
  base64 → tmp 文件 → ffmpeg 转 16kHz mono wav → run_whisper_cpp → 返
  {text, duration_sec, original_filename}
- src-tauri/src/lib.rs: 注册 transcribe_audio_from_b64
- src/tabs/Chat/ChatInput.tsx: classifyFile 加 audio 分支 (mp3/m4a/wav/...) +
  100MB 大小放宽 + parseLabel 区分 '🎙 正在转录音频...' + FileChip 显示
  '🎵 5分20秒 · 转录 N 字'
- src/lib/chat.ts: audio 分支 early return — 不要 'execute_code 读完整' 提示
  (转录就是全文, 没 keptPath)

──────────── ARCH2 + ARCH1 P3 配套 ────────────

(之前 BL-ARCH1+ARCH2 commit 1308754 ship 时漏掉的尾巴)
- src/lib/me.ts: Role 加 sysadmin
- src/lib/env.ts: webUrl 配置 + bootstrap 接 web_url 字段
- src/tabs/Dashboard/DashboardTab.tsx: 砍 7 张管理类卡 (放老 commit 但显示 staged)
- src/tabs/Dashboard/WebPortalLink.tsx: 顶部 banner + 心跳 + role 过滤

鸿波诊断功劳 #13~17:
13. 'piper 这么好玩没理由不现在做' → BL-VOICE2 P0
14. 'brew install piper-tts' formula 不存在 → fix1 venv 真路子
15. 'PEP 668 拦 + 7890 代理失败' → fix2 venv 绕开
16. 'high 模型 15 字节假文件' → fix5 LFS pointer 校验
17. '断句不理想是不是模型不好' → fix6 长句插逗号 (不需 jieba)
18. '拖 mp3 不支持' → BL-VOICE3 拖音频转文字
"

# ════════════════════════════════════════
# Commit #2: BL-Q3-FACT 设计文档
# ════════════════════════════════════════
echo
echo "════════════════════════════════════════"
echo "Commit #2: BL-Q3-FACT 事实补丁系统设计文档"
echo "════════════════════════════════════════"

git add docs/CATFISH-FACT-PATCH-DESIGN.md

git commit -m "BL-Q3-FACT (5/10 设计草案): 事实补丁系统 — '常变是央企/政府常态'

鸿波 5/10 夜读 a16z continual learning 文章后定调:
catfish 卖点不应停在'接 LLM', 而是'跟得上你们公司变化的伙伴'.
设计文档先行, 实施 Q3 启动 (5/14 demo + 6 月数据反馈后正式立项).

新文件 docs/CATFISH-FACT-PATCH-DESIGN.md (~600 行 11 章 + 附录 FAQ):

一. 背景与问题域 (央企'常变'本质 + 通用 LLM 卡训练 cutoff + 当前 skill 暗坑)
二. 价值主张 (跟 a16z module 层对齐 + 跟通用 LLM/企业知识库厂家差异化)
三. 用户故事 (合规小李 / 业务老王 / 员工小赵 / sysadmin 鸿波)
四. 系统架构 (5 层组件 + 跟现有系统对应表 70% 复用)
五. 数据模型 (4 张新 PG 表 + skill_revisions 加 trigger_source 列)
六. 实施阶段 (P0 1.5-2 周 / P1 2-3 周 / P2 3-4 周)
七. 现有系统复用 (BL-MM13/14/15 + BL-D2 + BL-ARCH1 P1 + BL-D17)
八. 风险 (7 个 + 缓解策略)
九. 商业策略 (单独定价分层方向)
十. 5/14 demo 演示脚本 (7 步剧本, 客户问到时直接念)
十一. 关键决策记录
附录 A. 内部对齐 FAQ (5 Q/A)

不开实施 task. 用作 5/14 demo 弹药 + 客户深度交流文档 + Q3 启动 work breakdown.

鸿波诊断功劳 #19: 上周客户'想用'但鸿波想'拖一阵', 用空档把这块设计透 →
不增加工作量, 反而把'拖延期'转化成产出.
"

# ════════════════════════════════════════
# Commit #3: 文档同步 (BACKLOG / .gitignore / sync 脚本)
# ════════════════════════════════════════
echo
echo "════════════════════════════════════════"
echo "Commit #3: 文档 + sync 脚本"
echo "════════════════════════════════════════"

git add docs/BACKLOG.md
git add .gitignore
git add scripts/sync-bl-arch.sh
git add scripts/sync-bl-voice-fact.sh   # 本脚本

git commit -m "docs: 5/10 夜 §M+§N 段更新 + sync 脚本归档

BACKLOG.md:
- §M BL-ARCH1/ARCH2 全部标 ✅ 完成 (5/10 一夜 ship)
- §N a16z continual learning 启发, 加 BL-Q3-FACT 设计文档链接

.gitignore: 加 nohup.out (skills-hub / mcp-registry runtime log)

scripts/:
- sync-bl-arch.sh (5/10 BL-ARCH1+ARCH2 那波用过, 归档)
- sync-bl-voice-fact.sh (本次脚本, 归档供以后参考)
"

# ════════════════════════════════════════
# Push
# ════════════════════════════════════════
echo
echo "════════════════════════════════════════"
echo "本地新 commit 列表 (待 push 到 origin/main):"
echo "════════════════════════════════════════"
git log origin/main..HEAD --oneline
echo
NEW_COUNT=$(git rev-list origin/main..HEAD --count)
echo "→ 共 $NEW_COUNT 个新 commit"
echo

if [ "$NEW_COUNT" -eq 0 ]; then
    echo "❎ 没新 commit, 检查上面有没有 git add 失败"
    exit 1
fi

read -p "确认 push 到 origin/main? [y/N] " yn
case $yn in
    [Yy]*)
        echo "→ pushing..."
        if git push origin main; then
            echo
            echo "✅ push 成功. GitHub 应该看得到 $NEW_COUNT 个新 commit."
            echo "   https://github.com/jinichen/catfish/commits/main"
        else
            echo
            echo "❌ push 失败. 常见原因:"
            echo "  1. 网络代理: 确认 git config --global http.proxy 设置或 unset"
            echo "  2. GitHub PAT 失效: 跑 gh auth status 或重新 login"
            echo "  3. 远程有别人新 commit: git pull --rebase origin main 后重试"
            exit 1
        fi
        ;;
    *)
        echo "❎ 取消 push. 3 个 commit 已本地落地, 后续手动 git push origin main"
        ;;
esac
