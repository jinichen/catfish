#!/usr/bin/env bash
# 装 catfish-feishu-monitor 到员工机器。
#
# 做：
#   1. 装 Python 包（editable，方便改代码即时生效）
#   2. 软链 catfish-feishu 到 ~/.local/bin
#   3. 生成默认 ~/.catfish/feishu.yaml
#   4. 可选：注册 launchd 后台常驻（macOS）
#
# 前置：跑过 catfish-browser-attach.sh（Catfish Chrome 就绪、CDP URL 写进 hermes config）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"

BOLD='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
step() { echo -e "${BOLD}[$1/$TOTAL]${RESET} $2"; }
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }
err()  { echo -e "    ${RED}错误${RESET} $*" >&2; }

AUTO_YES=0
SKIP_DAEMON=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) AUTO_YES=1 ;;
        --skip-daemon) SKIP_DAEMON=1 ;;
    esac
done

confirm() {
    if [ "$AUTO_YES" = "1" ]; then return 0; fi
    local reply
    read -r -p "    $1 [Y/n] " reply
    [ -z "$reply" ] || [[ "$reply" =~ ^[Yy] ]]
}

TOTAL=5
echo -e "${BOLD}catfish-feishu-monitor installer${RESET}"
echo

# 1. 找 Python / venv（优先员工已有的 catfish venv）
step 1 "找 Python"
CATFISH_VENV="$HOME/.catfish/venv"
if [ -x "$CATFISH_VENV/bin/python" ]; then
    VENV_PY="$CATFISH_VENV/bin/python"
    ok "复用已有 ~/.catfish/venv"
else
    PYTHON_BIN=""
    for b in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$b" >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$b")"
            break
        fi
    done
    if [ -z "$PYTHON_BIN" ]; then
        err "找不到 Python 3.9+"
        exit 1
    fi
    "$PYTHON_BIN" -m venv "$CATFISH_VENV"
    VENV_PY="$CATFISH_VENV/bin/python"
    ok "新建 venv：$CATFISH_VENV ($("$VENV_PY" --version))"
fi

# 2. pip install
step 2 "装 catfish-feishu-monitor"
"$VENV_PY" -m pip install -q -U pip
"$VENV_PY" -m pip install -q -e "$SCRIPT_DIR"
ok "$(${VENV_PY%/*}/catfish-feishu --help 2>&1 | head -1)"

# 3. 软链到 ~/.local/bin
step 3 "软链 catfish-feishu 到 ~/.local/bin"
mkdir -p "$LOCAL_BIN"
ln -sf "${VENV_PY%/*}/catfish-feishu" "$LOCAL_BIN/catfish-feishu"
ok "$LOCAL_BIN/catfish-feishu"
case ":$PATH:" in
    *":$LOCAL_BIN:"*) ;;
    *)
        warn "~/.local/bin 不在 PATH 里。加到 ~/.zshrc："
        echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

# 4. 生成默认配置（如果没有）
step 4 "初始化 ~/.catfish/feishu.yaml"
if [ -f "$HOME/.catfish/feishu.yaml" ]; then
    ok "已存在，保留员工自定义"
else
    "$VENV_PY" -c "from catfish_feishu.config import ensure_config_exists; ensure_config_exists()"
    ok "已生成默认配置"
    warn "记得编辑 ~/.catfish/feishu.yaml 把你的姓名 / 项目关键词填进去"
    echo "        catfish-feishu config"
fi

# 5. launchd 常驻（可选）
step 5 "后台常驻（launchd）"
if [ "$SKIP_DAEMON" = "1" ]; then
    ok "跳过（--skip-daemon）。以后手动启：catfish-feishu start"
elif [ "$(uname -s)" != "Darwin" ]; then
    ok "非 macOS，跳过 launchd。Linux 用户参考 edge/local-search 的 systemd 用户服务写法。"
elif confirm "装 launchd 让 monitor 开机自启 + 崩溃自重启吗？"; then
    PLIST="$HOME/Library/LaunchAgents/ai.catfish.feishu-monitor.plist"
    mkdir -p "$(dirname "$PLIST")"
    LOG_PATH="$HOME/.catfish/feishu-monitor.log"
    mkdir -p "$(dirname "$LOG_PATH")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>ai.catfish.feishu-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PY</string>
        <string>-m</string>
        <string>catfish_feishu.cli</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>$LOG_PATH</string>
    <key>StandardErrorPath</key><string>$LOG_PATH</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$PATH</string>
    </dict>
</dict>
</plist>
EOF
    /bin/launchctl unload "$PLIST" >/dev/null 2>&1 || true
    /bin/launchctl load "$PLIST"
    ok "已装载。日志：$LOG_PATH"
else
    ok "跳过。以后手动启：catfish-feishu start"
fi

cat <<EOF

=== 装好了 ===

下一步：
  1. catfish-feishu config               # 编辑关键词，填你的姓名 / 项目名
  2. catfish-feishu status                # 看 CDP 是否可达、飞书 tab 是否在
  3. 在 Catfish Chrome 里打开飞书 Web 登录（以后不用关）
  4. catfish-feishu test                  # 模拟一条消息跑通全链路

日常：
  catfish-feishu inbox                    # 看今天哪些消息被过滤出来
  catfish-feishu drafts                   # 看鲶鱼帮你起的回复草稿
  tail -f ~/.catfish/feishu-monitor.log   # 看 monitor 实时日志
EOF
