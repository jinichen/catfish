#!/usr/bin/env bash
# =============================================================
# catfish-browser-attach
# =============================================================
#
# 把 Hermes 的 browser 工具链切换到"attach 员工已有 Chrome"模式，
# 替换默认的"每次启新 headless Chromium"（首次 30~60 秒 + 登录态丢失）。
#
# 做的事：
#   1. 检查 Chrome 是否以 --remote-debugging-port=9222 方式运行
#   2. 没运行就帮员工启动一个（带恢复上次会话 + tabs）
#   3. 从 http://$CATFISH_CHROME_DEBUG_HOST:$CATFISH_CHROME_DEBUG_PORT/json/version 抓 webSocketDebuggerUrl
#      (默认 127.0.0.1:9222，可由 env var 覆写)
#   4. 幂等地写进 ~/.hermes/config.yaml 的 browser.cdp_url
#   5. 提示员工下次 hermes browser_navigate 就 <3 秒了
#
# 不需要 sudo。支持 macOS / Linux。Windows 用 .ps1 版本。
# =============================================================

set -euo pipefail

PORT="${CATFISH_CHROME_DEBUG_PORT:-9222}"
HOST="${CATFISH_CHROME_DEBUG_HOST:-127.0.0.1}"
DEBUG_URL="http://${HOST}:${PORT}/json/version"
HERMES_CONFIG="$HOME/.hermes/config.yaml"

# Chrome 的安全策略（2024 起）：默认 profile 下 --remote-debugging-port 被静默忽略，
# 以防恶意软件劫持员工日常登录态。**必须用独立 profile 目录**绕过这个限制。
# 副作用：这个 Chrome 实例和员工日常 Chrome 完全隔离，登录态要员工首次手动登一下。
CHROME_PROFILE_DIR="$HOME/.catfish/chrome-profile"

# ---------- 打印工具 ----------

BOLD='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
step() { echo -e "${BOLD}[$1/5]${RESET} $2"; }
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }
err()  { echo -e "    ${RED}错误${RESET} $*" >&2; }

# ---------- 平台适配 ----------

detect_chrome_cmd() {
    case "$(uname -s)" in
        Darwin)
            for p in \
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
                "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary" \
                "/Applications/Chromium.app/Contents/MacOS/Chromium"
            do
                [ -x "$p" ] && { echo "$p"; return 0; }
            done
            ;;
        Linux)
            for bin in google-chrome google-chrome-stable chromium chromium-browser; do
                command -v "$bin" >/dev/null 2>&1 && { command -v "$bin"; return 0; }
            done
            ;;
    esac
    return 1
}

# ---------- 1. 先看 9222 是不是已经活着 ----------

step 1 "检查 Chrome 是否已在调试模式运行"
if curl -sSf -m 2 "$DEBUG_URL" >/dev/null 2>&1; then
    ok "已检测到 Chrome 在 :$PORT 开着调试端口，跳过启动"
    CHROME_ALREADY_UP=1
else
    CHROME_ALREADY_UP=0
    ok "9222 未开，准备启动 Chrome"
fi

# ---------- 2. 没开就启 ----------

step 2 "启动 Chrome（带调试端口）"
if [ "$CHROME_ALREADY_UP" = "0" ]; then
    CHROME_BIN="$(detect_chrome_cmd || true)"
    if [ -z "$CHROME_BIN" ]; then
        err "没找到 Chrome / Chromium。先装一个再来。"
        exit 1
    fi

    # 注意：我们用独立 profile 目录（$CHROME_PROFILE_DIR），所以**跟员工日常 Chrome 不冲突**。
    # 不需要让员工关掉日常 Chrome。两个 Chrome 实例可以并存。
    mkdir -p "$CHROME_PROFILE_DIR"

    # 平台分叉启动 Chrome
    case "$(uname -s)" in
        Darwin)
            # macOS：用 open -na --args 启动 .app 专用 profile 实例
            # 关键 flag：
            #   --user-data-dir：独立 profile（绕过 Chrome 对默认 profile 的调试端口禁用）
            #   --no-first-run：不弹欢迎引导
            #   --no-default-browser-check：不问是否设为默认
            echo "    启动命令：open -na 'Google Chrome' --args --remote-debugging-port=$PORT --user-data-dir=$CHROME_PROFILE_DIR"
            open -na "Google Chrome" --args \
                --remote-debugging-port="$PORT" \
                --user-data-dir="$CHROME_PROFILE_DIR" \
                --no-first-run \
                --no-default-browser-check \
                --restore-last-session
            ;;
        Linux)
            echo "    启动命令：$CHROME_BIN --remote-debugging-port=$PORT --user-data-dir=$CHROME_PROFILE_DIR"
            nohup "$CHROME_BIN" \
                --remote-debugging-port="$PORT" \
                --user-data-dir="$CHROME_PROFILE_DIR" \
                --no-first-run \
                --no-default-browser-check \
                --restore-last-session \
                >/dev/null 2>&1 &
            ;;
    esac

    # 等 Chrome 就绪（最多 30 秒 —— 恢复多 tab 时可能要更久）
    for i in $(seq 1 60); do
        if curl -sSf -m 1 "$DEBUG_URL" >/dev/null 2>&1; then
            ok "Chrome 就绪（约 $((i * 500))ms）"
            break
        fi
        sleep 0.5
    done

    if ! curl -sSf -m 1 "$DEBUG_URL" >/dev/null 2>&1; then
        err "Chrome 启动了但 30 秒内 9222 没通。"
        echo ""
        echo "    自己诊断一下："
        echo "        pgrep -fl 'Google Chrome'       # 进程在不在"
        echo "        lsof -iTCP:$PORT -sTCP:LISTEN   # 9222 有没有人监听"
        echo "        curl -v http://${HOST}:${PORT}/json/version  # 看详细错误"
        echo ""
        echo "    可能原因："
        echo "        1. Chrome 正在恢复大量 tab，30 秒还没完。等它完再 curl 看看，通了就手动跑第 3/4 步"
        echo "        2. Chrome 启动时弹了个更新 / 引导页挡着。点掉再试"
        echo "        3. 之前残留的 Chrome 进程占着 profile 目录。退干净再试"
        exit 3
    fi
else
    ok "跳过启动"
fi

# ---------- 3. 抓 webSocketDebuggerUrl ----------

step 3 "抓 CDP WebSocket URL"
WS_URL="$(curl -s "$DEBUG_URL" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("webSocketDebuggerUrl", ""))
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(1)
')"

if [ -z "$WS_URL" ]; then
    err "没从 $DEBUG_URL 拿到 webSocketDebuggerUrl。手动 curl 试试看是什么响应。"
    exit 4
fi
ok "$WS_URL"

# ---------- 4. 写进 ~/.hermes/config.yaml ----------

step 4 "写入 $HERMES_CONFIG 的 browser.cdp_url"
mkdir -p "$(dirname "$HERMES_CONFIG")"
touch "$HERMES_CONFIG"

python3 - "$HERMES_CONFIG" "$WS_URL" <<'PYEOF'
"""幂等地把 browser.cdp_url 写进 ~/.hermes/config.yaml，保留其他字段。"""
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    需要 pyyaml。跑 pip install pyyaml 再重试。")
    sys.exit(5)

cfg_path = Path(sys.argv[1])
ws_url = sys.argv[2]

text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}

browser = data.get("browser") or {}
if not isinstance(browser, dict):
    browser = {}
old = browser.get("cdp_url")
browser["cdp_url"] = ws_url
data["browser"] = browser

cfg_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
    encoding="utf-8",
)
print(f"    {'更新' if old else '新增'}：browser.cdp_url")
if old and old != ws_url:
    print(f"    旧值：{old}")
    print(f"    新值：{ws_url}")
PYEOF

# ---------- 5. 总结 ----------

step 5 "完成"
# heredoc 里混中文和 $var 在某些 bash 版本下会踩到 set -u 的 Unicode 边界 bug，
# 临时关掉 -u，EOF 后恢复。不是功能 bug，只是打印提示的 UX 毛刺。
set +u
cat <<EOF

    ✓ 专用 Chrome 实例已启，profile 目录：$CHROME_PROFILE_DIR
    ✓ Hermes 下次 browser_navigate 从 30~60 秒变 <3 秒
    ✓ 和你日常 Chrome 完全隔离（两个 Chrome 图标同时在 Dock）

    隐私模型（跟你原以为的不一样，看这里）：
      · 这是一个**专用 Chrome profile**，不是你日常 Chrome
      · Hermes 只能看到这个专用 Chrome 里的东西
      · 你日常 Chrome 的登录态 / cookies / 书签 / 扩展**完全不被看到**
      · 要让 Hermes 能访问公司 Jira / Confluence，**在这个专用 Chrome 里单独登一次**
        登录态会存在 $CHROME_PROFILE_DIR，下次 attach 自动复用
      · 这个设计比"让 Hermes 共用日常 Chrome"**更安全**

    首次使用建议：
      1. 在这个专用 Chrome 里打开公司 Jira / Confluence / GitLab 之类登一下
      2. 登录态就持久化到 profile 里
      3. 以后 Hermes 自动带着这份登录态做事

    验证方式：
      hermes
      > 帮我打开 github.com 看首页
      （首次应该 <3 秒）

    注意事项：
      1. 不要关掉这个专用 Chrome（关了就断 attach，下次要重跑本脚本）
      2. 每次 Chrome 换了 PID，WS URL 里的 UUID 会变，要重跑本脚本刷新 config
      3. 你的日常 Chrome 照常用，不受影响

    卸载：
      bash $(dirname "$0")/catfish-browser-detach.sh        # 只拆 Hermes 端配置
      rm -rf $CHROME_PROFILE_DIR                            # 删专用 profile（登录态一起清）
EOF
set -u
