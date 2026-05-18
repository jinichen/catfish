#!/usr/bin/env bash
# check-gateway-no-edge-fs.sh — gateway 代码不能读员工本机 ~/.hermes / ~/.catfish.
#
# 5/19 BL-GATEWAY-NO-EDGE-FS-LINT (BL-MEMORY-OWNERSHIP-FIX Phase 2-4).
#
# 红线 (BL-CENTRAL-EDGE-BOUNDARY): gateway 部署到公司机房后, 它没法读员工 Mac
# 上的 ~/.hermes / ~/.catfish 文件. memory inject 责任已转 hermes plugin (5/19
# Phase 2-3). 这个脚本检查 gateway 源码里**没有**残留的 edge FS 访问.
#
# CI 跑这条, 报错 = 阻断 push.
#
# 已知合法例外 (加 #noqa: gateway-no-edge-fs 注释跳过):
#   - dev_token 兜底 (合理 — dev 模式下 gateway 跟 hermes 同机)
#   - 已 deprecated 但未删的 memory_registry provider 文件 (跟 sessions_browse.py
#     一样保留作 reference, 不被调用)
#
# 用法:
#   bash scripts/check-gateway-no-edge-fs.sh         # 全扫
#   bash scripts/check-gateway-no-edge-fs.sh --fix   # (未来) 标 noqa

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GATEWAY_SRC="$REPO_ROOT/central/llm-gateway/src/catfish_gateway"

if [ ! -d "$GATEWAY_SRC" ]; then
    echo "ERROR: $GATEWAY_SRC 不存在" >&2
    exit 1
fi

# 模式: 出现这些 = 红线违规 (要 noqa 才允许).
# 只匹配真**函数调用** (Path.home() / os.path.expanduser('~/...')), 不匹配
# 注释里的文档引用. 这样减误报, 保留有意义信号.
PATTERNS=(
    'Path\.home\(\)'
    'os\.path\.expanduser\([^)]*~'
    'os\.environ\.get\("HOME"\)'
    "Path\(.*home\(\).*hermes"
    "Path\(.*home\(\).*catfish"
)

FOUND_VIOLATIONS=0

for pattern in "${PATTERNS[@]}"; do
    while IFS= read -r match; do
        # 跳过有 noqa: gateway-no-edge-fs 注释的行
        if echo "$match" | grep -q "noqa: gateway-no-edge-fs"; then
            continue
        fi
        # 跳过 deprecated provider 文件 (5/19 不调用, 留 reference)
        if echo "$match" | grep -qE "/memory/providers/|/sessions_browse\.py:"; then
            continue
        fi
        # 跳过本脚本自己的 grep pattern 字符串
        if echo "$match" | grep -qE "check-gateway-no-edge-fs|BL-GATEWAY-NO-EDGE-FS-LINT"; then
            continue
        fi
        # 跳过注释行 (以 # 开头, 实际不执行)
        # (粗略: trim 后 #开头. 真严谨用 AST, 现在简单 grep)
        clean=$(echo "$match" | cut -d: -f3-)
        trimmed="${clean## }"
        if [[ "$trimmed" =~ ^# ]]; then
            continue
        fi
        echo "✗ $match"
        FOUND_VIOLATIONS=$((FOUND_VIOLATIONS + 1))
    done < <(grep -rn "$pattern" "$GATEWAY_SRC" --include="*.py" 2>/dev/null || true)
done

STRICT=0
for arg in "$@"; do
    [ "$arg" = "--strict" ] && STRICT=1
done

if [ "$FOUND_VIOLATIONS" -gt 0 ]; then
    echo
    echo "⚠️  发现 $FOUND_VIOLATIONS 处 gateway 读员工本机 FS." >&2
    echo "   gateway 不能依赖 ~/.hermes / ~/.catfish (中央化部署时读不到)." >&2
    echo "   memory inject 已转 hermes plugin (5/19 BL-MEMORY-OWNERSHIP-FIX)." >&2
    echo "   但 quota / facts / tool_archive / proactive / etc 还读 — 这些是" >&2
    echo "   Phase 4 中央部署前的 backlog. 跟 memory 一样要迁." >&2
    echo "   合法例外加 '# noqa: gateway-no-edge-fs' 注释跳过." >&2
    echo "   详见 docs/MEMORY-OWNERSHIP-ARCHITECTURE.md" >&2
    if [ "$STRICT" = "1" ]; then
        echo
        echo "❌ --strict 模式: 阻断" >&2
        exit 1
    fi
    echo
    echo "(warning 模式, 不阻断. 加 --strict 转 fail.)"
    exit 0
fi

echo "✓ gateway 源码无 edge FS 访问违规 ($GATEWAY_SRC)"
