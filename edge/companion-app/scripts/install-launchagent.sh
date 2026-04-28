#!/usr/bin/env bash
# install-launchagent.sh — 把 Catfish Companion 注册成 macOS 开机自启
#
# 安装路径: ~/Library/LaunchAgents/com.catfish.companion.plist
# 用 launchctl bootstrap (新 API, 替代旧 launchctl load)
#
# 用法:
#   bash install-launchagent.sh           # 装
#   bash install-launchagent.sh --check   # 看现状 (装了没 / 在跑没)
#   bash install-launchagent.sh --uninstall  # 卸
#
# 设计:
#   - .app 路径动态解析: 优先 /Applications/Catfish Companion.app (生产),
#     fallback target/release/bundle/macos/ (开发)
#   - 防重复安装: 已存在 plist 时先 bootout 再 bootstrap, 不抛
#   - RunAtLoad=true (开机自启) + KeepAlive=true (崩溃自动拉起)

set -euo pipefail

PLIST_LABEL="com.catfish.companion"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/Catfish Companion"
LOG_OUT="${LOG_DIR}/companion.out.log"
LOG_ERR="${LOG_DIR}/companion.err.log"

# 颜色
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BOLD=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi

# ---------- 找 .app ----------
find_app_binary() {
    local prod="/Applications/Catfish Companion.app/Contents/MacOS/catfish-companion-app"
    if [ -x "$prod" ]; then
        echo "$prod"
        return 0
    fi

    # 开发场景: target/release/bundle/macos/
    local script_dir; script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local dev="${script_dir}/../src-tauri/target/release/bundle/macos/Catfish Companion.app/Contents/MacOS/catfish-companion-app"
    if [ -x "$dev" ]; then
        echo "$dev"
        return 0
    fi

    return 1
}

# ---------- 子命令 ----------

cmd_check() {
    echo "${BOLD}[Catfish Companion · LaunchAgent 状态]${RESET}"
    echo

    if [ -f "$PLIST_PATH" ]; then
        echo "    plist:    ${GREEN}已装${RESET} ($PLIST_PATH)"
    else
        echo "    plist:    ${YELLOW}未装${RESET}"
    fi

    if launchctl list | grep -q "$PLIST_LABEL"; then
        local pid; pid="$(launchctl list | grep "$PLIST_LABEL" | awk '{print $1}')"
        if [ "$pid" = "-" ]; then
            echo "    runtime:  ${YELLOW}已注册但当前没跑${RESET}"
        else
            echo "    runtime:  ${GREEN}在跑 (PID $pid)${RESET}"
        fi
    else
        echo "    runtime:  ${YELLOW}未注册${RESET}"
    fi

    if app="$(find_app_binary)"; then
        echo "    binary:   ${GREEN}找到${RESET} $app"
    else
        echo "    binary:   ${RED}找不到${RESET} (没 build .app? 跑 npm run tauri build)"
    fi

    echo
    echo "${BOLD}日志路径${RESET}: $LOG_DIR"
}

cmd_install() {
    local app_binary
    if ! app_binary="$(find_app_binary)"; then
        echo "${RED}找不到 Catfish Companion.app${RESET}" >&2
        echo "  期望路径之一:" >&2
        echo "    - /Applications/Catfish Companion.app" >&2
        echo "    - ./src-tauri/target/release/bundle/macos/Catfish Companion.app" >&2
        echo "  跑 ${BOLD}npm run tauri build${RESET} 先 build 出来, 或者 cp 到 /Applications/" >&2
        exit 1
    fi

    mkdir -p "$LOG_DIR"
    mkdir -p "$(dirname "$PLIST_PATH")"

    # 已装 → 先 bootout 再重装 (idempotent)
    if launchctl list | grep -q "$PLIST_LABEL"; then
        echo "${YELLOW}已注册过, 先 bootout...${RESET}"
        launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
    fi

    # 写 plist (别用 PlistBuddy, 直接 heredoc 简单)
    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${app_binary}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
EOF

    chmod 644 "$PLIST_PATH"

    # bootstrap 加载 (新 API)
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

    echo "${GREEN}✓ 装好了${RESET}"
    echo "    plist: $PLIST_PATH"
    echo "    binary: $app_binary"
    echo "    日志: $LOG_OUT (stdout) / $LOG_ERR (stderr)"
    echo
    echo "下次开机会自动启动. 要现在测一下:"
    echo "    bash install-launchagent.sh --check"
}

cmd_uninstall() {
    if launchctl list | grep -q "$PLIST_LABEL"; then
        echo "bootout..."
        launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
    fi

    if [ -f "$PLIST_PATH" ]; then
        rm -f "$PLIST_PATH"
        echo "${GREEN}✓ 卸了${RESET} ($PLIST_PATH)"
    else
        echo "${YELLOW}plist 本来就不在${RESET}"
    fi

    echo
    echo "注: 本地日志 $LOG_DIR 没动 (员工想看历史还能用). 要清:"
    echo "    rm -rf '$LOG_DIR'"
}

# ---------- 主分发 ----------

case "${1:-install}" in
    install|"")
        cmd_install
        ;;
    --check|status|check)
        cmd_check
        ;;
    --uninstall|uninstall|remove)
        cmd_uninstall
        ;;
    -h|--help|help)
        cat <<'EOF'
install-launchagent.sh — Catfish Companion 开机自启

用法:
    bash install-launchagent.sh                 # 装 (默认)
    bash install-launchagent.sh --check         # 看状态
    bash install-launchagent.sh --uninstall     # 卸

会做什么:
    - 写 ~/Library/LaunchAgents/com.catfish.companion.plist
    - launchctl bootstrap 加载, 立即生效 + 开机自启
    - RunAtLoad=true + KeepAlive (异常退出自动拉起)
    - 日志写 ~/Library/Logs/Catfish Companion/

不会做什么:
    - 不动 /Applications/Catfish Companion.app (那是另一回事)
    - 不动 ~/.catfish / ~/.hermes (员工数据)
    - 不要求 sudo (LaunchAgent 跑在 GUI session, 员工权限就够)
EOF
        ;;
    *)
        echo "未知子命令: $1" >&2
        echo "用 -h 看帮助" >&2
        exit 1
        ;;
esac
