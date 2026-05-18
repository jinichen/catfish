#!/usr/bin/env bash
# install-catfish-memory.sh — 把 catfish-memory hermes plugin 装进
# ~/.hermes/hermes-agent/plugins/memory/, 通过软链方式 (避免 hermes 升级时
# 覆盖 plugin).
#
# 装完会建议员工: hermes memory setup 激活 (或我们直接改 ~/.hermes/config.yaml
# 的 memory.provider).
#
# 5/19 凌晨 (BL-CATFISH-MEMORY-PLUGIN-INSTALL-SCRIPT). 跟
# install-catfish-autocompress.sh 同模式.

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

TOTAL=5

echo -e "${BOLD}catfish-memory hermes plugin installer${RESET}"
echo

# 1. 前置检查
step 1 "检查 Hermes 安装"
if [ ! -d "$HERMES_AGENT_DIR" ]; then
    err "找不到 $HERMES_AGENT_DIR. 先装 Hermes 再来 (https://hermes.fm)"
    exit 1
fi
if [ ! -d "$HERMES_AGENT_DIR/plugins/memory" ]; then
    err "$HERMES_AGENT_DIR/plugins/memory 不存在 — Hermes 版本太老 (需要 0.13+ memory provider 体系)"
    exit 1
fi
ok "Hermes agent: $HERMES_AGENT_DIR"
ok "plugins/memory/ 目录存在"

# 2. 软链 catfish-memory
step 2 "软链 catfish-memory 到 Hermes plugins/memory/"
SRC="$SCRIPT_DIR/catfish-memory"
DST="$HERMES_AGENT_DIR/plugins/memory/catfish-memory"

if [ ! -d "$SRC" ]; then
    err "$SRC 不存在"
    exit 1
fi

# 幂等: 旧的软链或目录先清掉
if [ -L "$DST" ] || [ -e "$DST" ]; then
    rm -rf "$DST"
fi
ln -s "$SRC" "$DST"
ok "$DST -> $SRC"

# 3. 自检 plugin 可加载
step 3 "自检 plugin 可加载"
VENV_PY="$HERMES_AGENT_DIR/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    warn "找不到 $VENV_PY, 跳过自检 (不影响装载, 只是失去 pre-flight 验证)"
else
    cd "$HERMES_AGENT_DIR"
    if "$VENV_PY" -c "
import sys
sys.path.insert(0, '.')
from plugins.memory import discover_memory_providers
found = False
for name, desc, avail in discover_memory_providers():
    if name == 'catfish-memory':
        found = True
        print(f'    {name}: available={avail}')
        if not avail:
            print('    WARNING: catfish-memory available=False — 检查 ~/.catfish/ 是否存在')
        break
if not found:
    raise SystemExit('discover_memory_providers 没找到 catfish-memory')
" 2>&1; then
        ok "plugin 加载成功"
    else
        err "plugin 加载失败, 看上面 traceback 定位"
        exit 1
    fi
fi

# 4. 跑 plugin 单测 (可选)
step 4 "(可选) 跑 plugin 单测"
if [ -f "$SRC/tests/test_catfish_memory.py" ] && [ -x "$VENV_PY" ]; then
    if confirm "跑 22 个 plugin 单测确认环境 OK?"; then
        if "$VENV_PY" -m pytest "$SRC/tests/" -q 2>&1 | tail -5; then
            ok "单测通过"
        else
            warn "单测失败但 plugin 安装不受影响"
        fi
    else
        ok "跳过单测"
    fi
else
    warn "跳过单测 (找不到 tests/ 或 hermes venv)"
fi

# 5. 激活 plugin (改 ~/.hermes/config.yaml)
step 5 "激活 catfish-memory (改 $HERMES_CONFIG)"
if ! confirm "把 memory.provider 改成 catfish-memory 激活?"; then
    cat <<EOF
    跳过激活. 以后要启用:
      方式 1: 跑 hermes memory setup, 交互选 catfish-memory
      方式 2: 编辑 $HERMES_CONFIG, 找到或新增:
              memory:
                provider: catfish-memory
    然后重启 hermes (如果在跑).
EOF
    exit 0
fi

mkdir -p "$(dirname "$HERMES_CONFIG")"
touch "$HERMES_CONFIG"

# 用 hermes venv 的 python (有 pyyaml) 改 config
"$VENV_PY" - "$HERMES_CONFIG" <<'PYEOF'
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("    需要 pyyaml. hermes venv 应该自带, 跳过自动激活, 请手动编辑.")
    sys.exit(2)

cfg = Path(sys.argv[1])
text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict):
    data = {}

# 备份
backup = cfg.with_suffix(cfg.suffix + ".bak.installer")
if cfg.exists():
    backup.write_text(text, encoding="utf-8")
    print(f"    备份: {backup}")

mem = data.get("memory") or {}
if not isinstance(mem, dict):
    mem = {}

old = mem.get("provider")
mem["provider"] = "catfish-memory"
data["memory"] = mem

cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

if old and old != "catfish-memory":
    print(f"    OK memory.provider: {old!r} → 'catfish-memory'")
else:
    print(f"    OK memory.provider: 'catfish-memory'")
PYEOF

# 重启 hermes (如果在跑)
if pgrep -f "hermes gateway\|hermes serve" > /dev/null; then
    warn "hermes 在跑, 你需要重启它让 catfish-memory 生效:"
    echo "      pkill -f 'hermes gateway'"
    echo "      hermes gateway   # 或 nohup hermes gateway > ~/.hermes/serve.log 2>&1 &"
fi

echo
ok "全部完成. 验证:"
echo "      hermes memory status         # 应该显 'catfish-memory ← active'"
echo
echo "详见 $SRC/README.md"
