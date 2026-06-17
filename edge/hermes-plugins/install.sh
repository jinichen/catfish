#!/usr/bin/env bash
# ⚠️ P3.5.17 (6/17 鸿波) DEPRECATED — catfish-autocompress 已退役.
#
#   退役原因: hermes 自带 ContextCompressor (~/.hermes/config.yaml context.engine
#   = compressor) 已经 cover preflight 压缩, catfish-autocompress 仓库源码
#   早已删 (./catfish-autocompress 目录不存在), ~/.hermes/...plugins/context_engine/
#   catfish-autocompress 是 dangling 软链. 真因鸿波 6/16 撞 304K 不压缩 = hermes
#   auxiliary_client 调 gateway 缺 X-Catfish-User header → 400 (P3.5.17.b 已修).
#
#   不再有 catfish 自定义 context engine. 留这两个脚本只供历史 uninstall 用 —
#   跑 ./uninstall.sh 清掉老员工机器上的 dangling 软链 + 把 config.yaml engine
#   字段切回 compressor.
#
# ── 历史 (deprecated install 流程, 保留 git blame 用) ─────────────────────────
# 把 catfish 自己的 Hermes 插件装进 ~/.hermes/hermes-agent/plugins/ 相应目录。
#
# 目前只有一个插件：catfish-autocompress（context engine）。
# 用软链方式装，避免 hermes update 把插件冲掉。
#
# 插件生效还需要改 ~/.hermes/config.yaml 把 context.engine 改成 catfish-autocompress。
# 本脚本会问员工要不要自动改（默认 Y）。
# ─────────────────────────────────────────────────────────────────────────

# P3.5.17 早 abort, 防新员工误装
echo ""
echo "⚠️  catfish-autocompress 已退役 (P3.5.17, 6/17 鸿波)."
echo "   hermes 自带 ContextCompressor 已 cover. 不再装 catfish-autocompress."
echo "   要清老 dangling 软链, 跑: bash $(dirname "$0")/uninstall.sh"
echo ""
exit 0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_AGENT_DIR="$HOME/.hermes/hermes-agent"
HERMES_CONFIG="$HOME/.hermes/config.yaml"

BOLD='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
step() { echo -e "${BOLD}[$1/$TOTAL]${RESET} $2"; }
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }
err()  { echo -e "    ${RED}错误${RESET} $*" >&2; }

AUTO_YES=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) AUTO_YES=1 ;;
        -h|--help)
            sed -n '2,11p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

confirm() {
    if [ "$AUTO_YES" = "1" ]; then return 0; fi
    local reply
    read -r -p "    $1 [Y/n] " reply
    [ -z "$reply" ] || [[ "$reply" =~ ^[Yy] ]]
}

TOTAL=4

echo -e "${BOLD}catfish Hermes 插件 installer${RESET}"
echo

# 1. 前置
step 1 "检查 Hermes 安装"
if [ ! -d "$HERMES_AGENT_DIR" ]; then
    err "找不到 $HERMES_AGENT_DIR。先装 Hermes 再来。"
    exit 1
fi
if [ ! -d "$HERMES_AGENT_DIR/plugins/context_engine" ]; then
    err "$HERMES_AGENT_DIR/plugins/context_engine 不存在 —— Hermes 版本太老？需要 0.10+"
    exit 1
fi
ok "Hermes agent: $HERMES_AGENT_DIR"

# 2. 软链 catfish-autocompress
step 2 "软链 catfish-autocompress 到 Hermes context_engine 目录"
SRC="$SCRIPT_DIR/catfish-autocompress"
DST="$HERMES_AGENT_DIR/plugins/context_engine/catfish-autocompress"
if [ ! -d "$SRC" ]; then
    err "$SRC 不存在"
    exit 1
fi
# 幂等：旧的软链或目录先清掉（避免 ln -sfn 在某些 bash 下不覆盖目录）
if [ -L "$DST" ] || [ -e "$DST" ]; then
    rm -rf "$DST"
fi
ln -s "$SRC" "$DST"
ok "$DST -> $SRC"

# 3. 自检 Python 能正确加载
step 3 "自检插件可加载"
VENV_PY="$HERMES_AGENT_DIR/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    warn "找不到 $VENV_PY，跳过自检（不影响装载，只是失去 pre-flight 验证）"
else
    cd "$HERMES_AGENT_DIR"
    if "$VENV_PY" -c "
import sys
sys.path.insert(0, '.')
from plugins.context_engine import load_context_engine
engine = load_context_engine('catfish-autocompress')
if engine is None:
    raise SystemExit('load_context_engine 返回 None')
assert engine.name == 'catfish-autocompress', f'name 不对：{engine.name}'
print(f'    engine.name = {engine.name}')
print(f'    threshold_percent = {engine.threshold_percent}')
" 2>&1; then
        ok "插件加载成功"
    else
        err "插件加载失败，看上面 traceback 定位"
        exit 1
    fi
fi

# 4. 改 config.yaml 切到这个 engine
step 4 "激活 catfish-autocompress（改 ~/.hermes/config.yaml）"
if ! confirm "把 context.engine 改成 catfish-autocompress 激活自动压缩吗？"; then
    cat <<EOF
    跳过激活。以后要启用：
        编辑 $HERMES_CONFIG，找到或新增：
            context:
              engine: catfish-autocompress
        然后重启 hermes。
EOF
    exit 0
fi

mkdir -p "$(dirname "$HERMES_CONFIG")"
touch "$HERMES_CONFIG"
python3 - "$HERMES_CONFIG" <<'PYEOF'
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    需要 pyyaml。跑 pip install pyyaml 再重试。")
    sys.exit(2)

cfg = Path(sys.argv[1])
text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}

ctx = data.get("context") or {}
if not isinstance(ctx, dict):
    ctx = {}
old = ctx.get("engine")
ctx["engine"] = "catfish-autocompress"
data["context"] = ctx

cfg.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
    encoding="utf-8",
)
verb = "更新" if old and old != "catfish-autocompress" else ("已存在" if old else "新增")
print(f"    {verb} context.engine = catfish-autocompress")
if old and old != "catfish-autocompress":
    print(f"    旧值：{old}")
PYEOF

cat <<EOF

=== 装好了 ===

重启 hermes 生效。启动日志里应该看到：
    [INFO] catfish.autocompress: catfish-autocompress 启用：threshold=70% ...

使用中当 prompt_tokens 达到 context_length × 0.70 时，Hermes 会自动触发压缩。
观察：gateway 日志里 prompt_tokens 应该周期性"降下来"而不是一路涨。

调阈值：
    export CATFISH_COMPRESS_THRESHOLD=0.65     # 更激进
    export CATFISH_COMPRESS_THRESHOLD=0.80     # 更懒
    然后重启 hermes

卸载：
    bash $SCRIPT_DIR/uninstall.sh
EOF
