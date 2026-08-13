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
    # 8/13: 原来这里直接 "手动处理" 就退出了, 于是它在本机以真目录的形态活了
    # 好几个月 —— 谁都没去手动处理。真实代价:
    #
    #   · approvals_bridge.py (P47, 269 行) 只存在于运行目录, 仓库里从来没有,
    #     换台机器就没了, 而且没有任何地方会报错
    #   · 仓库里改了 plugin.py, 运行的还是旧副本, 表现是"改了没效果"(军规 § 4.2)
    #   · deploy.sh 第 4 步跑 $TARGET/tests/, 副本里根本没有 tests/ 目录
    #
    # 改成: **先逐字节比对**, 一致就自动转软链 (转之前留备份); 不一致就把差异
    # 打出来让人看, 绝不盲目覆盖 —— 运行目录里可能有仓库没有的东西, 那正是
    # 上面第一条踩过的坑。
    echo "→ $TARGET 是真目录不是软链, 比对内容..."

    DIFFS=""
    while IFS= read -r f; do
        rel="${f#"$TARGET"/}"
        case "$rel" in *__pycache__*|*.pyc|*.bak-*) continue;; esac
        if [[ ! -e "$SRC_DIR/$rel" ]]; then
            DIFFS+="  仅运行目录有: $rel"$'\n'
        elif ! cmp -s "$f" "$SRC_DIR/$rel"; then
            DIFFS+="  内容不同:     $rel"$'\n'
        fi
    done < <(find "$TARGET" -type f)

    if [[ -n "$DIFFS" ]]; then
        echo "✗ 运行目录跟仓库不一致, 不敢覆盖:"
        echo "$DIFFS"
        echo "  处理顺序: 先把'仅运行目录有'的文件收回仓库 (git add), 再重跑本脚本。"
        echo "  ⚠ 直接 rm -rf 会永久丢掉那些文件 —— 它们没有第二份。"
        exit 1
    fi

    BACKUP="$HOME/.hermes/catfish-xcatfish-user.pre-symlink-$(date +%Y%m%d-%H%M%S)"
    # 备份放 plugins/ **外面**: 放里面 hermes 会把它当成另一个插件去发现加载
    cp -R "$TARGET" "$BACKUP"
    rm -rf "$TARGET"
    ln -s "$SRC_DIR" "$TARGET"
    echo "✓ 内容一致, 已转成软链 (备份: $BACKUP)"
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
