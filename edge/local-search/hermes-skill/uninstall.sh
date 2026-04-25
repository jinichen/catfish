#!/usr/bin/env bash
# 从当前用户的 Hermes 里干净卸载 catfish-local-search。
#
# 卸的东西（依次）：
#   1. ~/.hermes/config.yaml 里 mcp_servers.catfish-local-search 段
#   2. ~/.hermes/skills/productivity/catfish-local-search 软链
#   3. ~/.hermes/skills/catfish/local-search（旧位置，如果还在）
#   4. ~/.hermes/SOUL.md 里早期注入的 <!-- catfish-local-search:begin/end --> 段
#   5. ~/.local/bin/catfish-search 软链（如果是我们装的）
#
# 不删 ~/.catfish/（索引库 + 配置文件），员工数据保留。
# 如果要彻底清理包括数据：`rm -rf ~/.catfish/`。

set -euo pipefail

HERMES_CONFIG="$HOME/.hermes/config.yaml"
SKILL_DST="$HOME/.hermes/skills/productivity/catfish-local-search"
LEGACY_SKILL_DST="$HOME/.hermes/skills/catfish/local-search"
SOUL_FILE="$HOME/.hermes/SOUL.md"
LOCAL_BIN_LINK="$HOME/.local/bin/catfish-search"
MCP_SERVER_NAME="catfish-local-search"

echo "=== catfish-local-search 卸载 ==="

# 1. 从 config.yaml 删 mcp_servers 段
if [ -f "$HERMES_CONFIG" ]; then
    if python3 - "$HERMES_CONFIG" "$MCP_SERVER_NAME" <<'PYEOF' 2>/dev/null
from __future__ import annotations
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    pyyaml 没装，跳过 config.yaml 清理")
    sys.exit(0)

cfg_path = Path(sys.argv[1])
name = sys.argv[2]
text = cfg_path.read_text(encoding="utf-8")
data = yaml.safe_load(text) or {}
servers = data.get("mcp_servers") or {}
if name in servers:
    del servers[name]
    if servers:
        data["mcp_servers"] = servers
    else:
        # mcp_servers 空了就整个 key 删掉
        data.pop("mcp_servers", None)
    cfg_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"    已从 {cfg_path} 删除 mcp_servers.{name}")
else:
    print(f"    {cfg_path} 中未注册 {name}，跳过")
PYEOF
    then
        :
    else
        echo "    (config.yaml 清理遇到错误，请手动检查)"
    fi
else
    echo "    $HERMES_CONFIG 不存在，跳过"
fi

# 2. 删 skill 软链
for p in "$SKILL_DST" "$LEGACY_SKILL_DST"; do
    if [ -L "$p" ] || [ -e "$p" ]; then
        rm -rf "$p"
        echo "    已删 $p"
    fi
done
# 空目录清理
rmdir "$HOME/.hermes/skills/catfish" 2>/dev/null || true

# 3. 从 SOUL.md 拆掉早期注入的段落（如果存在）
if [ -f "$SOUL_FILE" ] && grep -qF '<!-- catfish-local-search:begin -->' "$SOUL_FILE"; then
    # 用 awk 把 begin/end marker 之间（含 marker）整段删掉
    awk '
        /<!-- catfish-local-search:begin -->/ { skip=1; next }
        /<!-- catfish-local-search:end -->/   { skip=0; next }
        !skip
    ' "$SOUL_FILE" > "$SOUL_FILE.tmp" && mv "$SOUL_FILE.tmp" "$SOUL_FILE"
    echo "    已从 $SOUL_FILE 删除 catfish 段落"
fi

# 4. 删 PATH 软链（只删指向 catfish 项目的那个）
if [ -L "$LOCAL_BIN_LINK" ]; then
    target="$(readlink "$LOCAL_BIN_LINK")"
    case "$target" in
        *catfish*) rm -f "$LOCAL_BIN_LINK"; echo "    已删 $LOCAL_BIN_LINK -> $target";;
        *) echo "    $LOCAL_BIN_LINK 指向 $target（非 catfish），保留";;
    esac
fi

cat <<'EOF'

=== 已卸载 ===

保留的东西（员工数据，不动）：
  ~/.catfish/            - 索引库和配置
  ~/.hermes/memories/    - 跨会话记忆（跟 catfish 无关）

彻底清理：
  rm -rf ~/.catfish/

重启 hermes 后 catfish-local-search 相关的 MCP tool 就消失了。
EOF
