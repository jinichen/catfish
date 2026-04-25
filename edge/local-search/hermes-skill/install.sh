#!/usr/bin/env bash
# 把 catfish-local-search 装到当前用户的 Hermes Agent 里。
#
# 装两样东西（互补，不冲突）：
#   1. MCP server：catfish-search-mcp 作为 Hermes 的原生 tool 出现，
#      模型直接看到 mcp_catfish_local_search_* 工具，自动使用，不用"哄"。
#   2. Skill 文档：~/.hermes/skills/productivity/catfish-local-search/
#      作为人类可读的使用说明，跟 tool 配套。
#
# 不需要 sudo。重复执行幂等。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/catfish-local-search"

# Hermes 0.10 只认预定义的一级 namespace（productivity / apple / github / ...），
# 自建一级目录（比如 catfish/）不会被加载。把 skill 放 productivity 下，文件夹名
# 保留 catfish- 前缀标识来源。未来 Hermes 支持自定义 namespace 后再挪。
HERMES_SKILLS_DIR="$HOME/.hermes/skills/productivity"
SKILL_DST="$HERMES_SKILLS_DIR/catfish-local-search"
LEGACY_SKILL_DST="$HOME/.hermes/skills/catfish/local-search"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
LOCAL_BIN="$HOME/.local/bin"
MCP_SERVER_NAME="catfish-local-search"

echo "=== catfish-local-search Hermes 集成安装 ==="

# --------------------------------------------------
# 1. 找 catfish-search 可执行文件
# --------------------------------------------------
echo "[1/5] 定位 catfish-search 可执行文件"
if ! command -v catfish-search >/dev/null 2>&1; then
    mkdir -p "$LOCAL_BIN"
    for candidate in \
        "$SCRIPT_DIR/../.venv/bin/catfish-search" \
        "$SCRIPT_DIR/../venv/bin/catfish-search" \
        "$HOME/person_task/catfish/edge/local-search/.venv/bin/catfish-search" \
        "$HOME/person_task/catfish/edge/local-search/venv/bin/catfish-search" \
        "$HOME/person_task/catfish/central/llm-gateway/venv/bin/catfish-search"
    do
        if [ -x "$candidate" ]; then
            ln -sf "$candidate" "$LOCAL_BIN/catfish-search"
            echo "    软链：$candidate -> $LOCAL_BIN/catfish-search"
            break
        fi
    done
    if ! [ -x "$LOCAL_BIN/catfish-search" ]; then
        echo "    找不到 catfish-search。先在 edge/local-search 下 pip install -e '.[all,mcp]'"
        exit 1
    fi
fi
CATFISH_SEARCH_BIN="$(command -v catfish-search)"
echo "    catfish-search: $CATFISH_SEARCH_BIN"

# 推断 catfish-search-mcp 应该在同一个 venv 的 bin 目录下
CATFISH_BIN_DIR="$(dirname "$(readlink -f "$CATFISH_SEARCH_BIN" 2>/dev/null || echo "$CATFISH_SEARCH_BIN")")"
MCP_BIN="$CATFISH_BIN_DIR/catfish-search-mcp"

if ! [ -x "$MCP_BIN" ]; then
    echo ""
    echo "    警告：找不到 $MCP_BIN"
    echo "    需要在 catfish-search 所在的 venv 里装 mcp 依赖并让新 entry point 生效："
    echo ""
    echo "        cd ~/person_task/catfish/edge/local-search"
    echo "        source <对应 venv>/bin/activate      # 比如 venv/ 或 ../../central/llm-gateway/venv/"
    echo "        pip install -e '.[mcp]'"
    echo ""
    echo "    装完后重跑这个 install.sh。"
    exit 1
fi
echo "    catfish-search-mcp: $MCP_BIN"

# --------------------------------------------------
# 2. 自检 MCP server 能启动
# --------------------------------------------------
echo "[2/5] 自检 MCP server"
# 发一条 initialize 请求，看能不能拿到 response（2 秒超时足够）
if timeout 3 bash -c "echo '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"installer\",\"version\":\"0\"}}}' | \"$MCP_BIN\"" >/dev/null 2>&1; then
    echo "    MCP server 握手 OK"
else
    echo "    MCP server 握手未返回，可能是 mcp 包没装。尝试快速导入测试..."
    if ! "$CATFISH_BIN_DIR/python" -c "import mcp" 2>/dev/null; then
        echo "    确认：mcp 包没装。在对应 venv 里跑 pip install 'mcp>=1.0'"
        exit 1
    fi
    echo "    mcp 包在，但握手失败；install 继续，用 'hermes mcp test' 再定位"
fi

# --------------------------------------------------
# 3. 装 skill 文档（人读的那份，跟 tool 配套）
# --------------------------------------------------
echo "[3/5] 装 skill 文档到 $SKILL_DST"
mkdir -p "$HERMES_SKILLS_DIR"
if [ -L "$LEGACY_SKILL_DST" ] || [ -d "$LEGACY_SKILL_DST" ]; then
    echo "    清理旧位置 $LEGACY_SKILL_DST"
    rm -rf "$LEGACY_SKILL_DST"
    rmdir "$HOME/.hermes/skills/catfish" 2>/dev/null || true
fi
ln -sfn "$SKILL_SRC" "$SKILL_DST"
echo "    完成：$SKILL_DST -> $SKILL_SRC"

# --------------------------------------------------
# 4. 注册到 ~/.hermes/config.yaml 的 mcp_servers 段
# --------------------------------------------------
echo "[4/5] 注册 MCP server 到 $HERMES_CONFIG"
mkdir -p "$(dirname "$HERMES_CONFIG")"
touch "$HERMES_CONFIG"

"$CATFISH_BIN_DIR/python" - "$HERMES_CONFIG" "$MCP_SERVER_NAME" "$MCP_BIN" <<'PYEOF'
"""幂等地把 MCP server 写进 ~/.hermes/config.yaml。"""
from __future__ import annotations
import sys
from pathlib import Path
import yaml

cfg_path = Path(sys.argv[1])
name = sys.argv[2]
command = sys.argv[3]

text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}

servers = data.setdefault("mcp_servers", {}) or {}
if not isinstance(servers, dict):
    print(f"警告：mcp_servers 不是 dict，备份后重建")
    backup = cfg_path.with_suffix(".yaml.bak")
    backup.write_text(text, encoding="utf-8")
    servers = {}

old = servers.get(name)
servers[name] = {
    "command": command,
    "args": [],
    "description": "Catfish 本地文件全文搜索（FTS5 索引，离线可用）",
    "enabled": True,
}
data["mcp_servers"] = servers

cfg_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
    encoding="utf-8",
)
print(f"    {'更新' if old else '新增'}：mcp_servers.{name} = {command}")
PYEOF

# --------------------------------------------------
# 5. 总结
# --------------------------------------------------
echo "[5/5] 完成。重启 hermes 生效。"
cat <<EOF

=== 装好了 ===

下一步：
  1. 完全退出当前 hermes（/exit 或 Ctrl+D）
  2. 重新启动：hermes
  3. 启动时你会看到 banner 多一行：catfish-local-search · N MCP
  4. Available Tools 段里应该出现 mcp_catfish_local_search_* 工具
  5. 测试："帮我找我电脑里关于鲶鱼的设计文档"
     预期 Hermes 直接调 mcp_catfish_local_search_local_search
     （不再跑 60 秒的 find/grep）

如果 tool 没出现，跑下面这个排障：
  hermes mcp list
  hermes mcp test $MCP_SERVER_NAME

卸载：
  bash $SCRIPT_DIR/uninstall.sh
EOF
