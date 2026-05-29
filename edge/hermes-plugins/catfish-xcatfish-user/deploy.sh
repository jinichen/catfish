#!/usr/bin/env bash
# Deploy catfish-xcatfish-user plugin: 拷贝 / 软链 到 ~/.hermes/plugins/
# 跟 catfish-memory 同 pattern.
set -euo pipefail

SRC_DIR="$HOME/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user"
HERMES_PLUGINS_DIR="$HOME/.hermes/plugins"
TARGET="$HERMES_PLUGINS_DIR/catfish-xcatfish-user"

echo "================================================================"
echo "  Deploy catfish-xcatfish-user plugin"
echo "================================================================"

# 1. 确认 plugin 源在 catfish 仓
if [[ ! -d "$SRC_DIR" ]]; then
    echo "✗ plugin 源目录不存在: $SRC_DIR"
    echo "   先把 outputs/catfish-xcatfish-user-plugin/ 整个搬到该路径"
    exit 1
fi

if [[ ! -f "$SRC_DIR/__init__.py" ]] || [[ ! -f "$SRC_DIR/plugin.py" ]]; then
    echo "✗ plugin 源目录缺文件 (__init__.py / plugin.py)"
    exit 1
fi

# 2. 确认 ~/.hermes/plugins 存在
mkdir -p "$HERMES_PLUGINS_DIR"

# 3. 检查现有 target — 是软链 / 真目录 / 不存在
if [[ -L "$TARGET" ]]; then
    echo "→ 已存在软链: $TARGET → $(readlink "$TARGET")"
    if [[ "$(readlink "$TARGET")" == "$SRC_DIR" ]]; then
        echo "✓ 软链已正确, 跳过"
    else
        echo "→ 软链指向不对, 重做"
        rm "$TARGET"
        ln -s "$SRC_DIR" "$TARGET"
        echo "✓ 重建软链: $TARGET → $SRC_DIR"
    fi
elif [[ -d "$TARGET" ]]; then
    echo "✗ $TARGET 是真目录不是软链, 手动处理: rm -rf $TARGET 后重跑"
    exit 1
else
    ln -s "$SRC_DIR" "$TARGET"
    echo "✓ 软链已建: $TARGET → $SRC_DIR"
fi

# 4. self-test (plugin 自己 verify hermes attribute)
echo ""
echo "→ 跑 plugin self-test (验证 hermes attribute 都还在)..."
cd "$HOME/.hermes/hermes-agent"
if "$HOME/.hermes/hermes-agent/venv/bin/python" -m pytest \
    "$TARGET/tests/test_patches_present.py" -v --tb=short 2>&1; then
    echo "✓ self-test 全通过"
else
    echo "✗ self-test 失败 — hermes 版本可能跟 plugin 不兼容"
    echo "  不要重启 hermes, 先看 failed test 输出, 修 plugin patch 点"
    exit 1
fi

# 5. config 提示
echo ""
echo "================================================================"
echo "  Deploy 完成. 接下来手动:"
echo ""
echo "  1. ~/.hermes/config.yaml 加 plugin 启用 (跟 catfish-memory 同位置):"
echo "       plugins:"
echo "         - catfish-memory  # 已启用"
echo "         - catfish-xcatfish-user  # 新加"
echo ""
echo "  2. 重启 hermes:"
echo "       launchctl kickstart -k gui/\$(id -u)/ai.hermes.gateway"
echo ""
echo "  3. 看启动 log 验证 plugin 加载:"
echo "       grep 'catfish-xcatfish-user' ~/.hermes/logs/agent.log"
echo "       期望: 'catfish-xcatfish-user plugin installed ✓ (11 patches applied)'"
echo ""
echo "  4. 跑 smoke test (3 路径端到端验证):"
echo "       bash $SRC_DIR/smoke-test.sh"
echo ""
echo "  5. 全部通过后再跑 revert 砍 hermes 仓现有 patch:"
echo "       bash $SRC_DIR/revert-hermes-patches.sh"
echo "================================================================"
