#!/usr/bin/env bash
# 卸载 catfish-feishu-monitor（保留 inbox / drafts 数据）。
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/ai.catfish.feishu-monitor.plist"
LINK="$HOME/.local/bin/catfish-feishu"
VENV="$HOME/.catfish/venv"

echo "=== catfish-feishu-monitor uninstaller ==="

if [ -f "$PLIST" ]; then
    /bin/launchctl unload "$PLIST" 2>/dev/null || true
    rm "$PLIST"
    echo "    已卸载 launchd: $PLIST"
fi

if [ -L "$LINK" ]; then
    rm "$LINK"
    echo "    已删软链 $LINK"
fi

if [ -x "$VENV/bin/pip" ]; then
    "$VENV/bin/pip" uninstall -y catfish-feishu-monitor 2>/dev/null || true
    echo "    已从 venv 卸载 catfish-feishu-monitor"
fi

cat <<'EOF'

保留的数据：
    ~/.catfish/feishu.yaml           (你的关键词配置)
    ~/.catfish/feishu-inbox/         (历史 inbox)
    ~/.catfish/feishu-drafts/        (历史草稿)

要彻底清理：
    rm -rf ~/.catfish/feishu.yaml ~/.catfish/feishu-inbox ~/.catfish/feishu-drafts
EOF
