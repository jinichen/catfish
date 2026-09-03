#!/usr/bin/env bash
# install-catfish-memory.sh — 把 catfish-memory hermes plugin 软链进
# ~/.hermes/plugins/ (**树外**), 让它扛得住 hermes 升级.
#
# 装完会建议员工: hermes memory setup 激活 (或我们直接改 ~/.hermes/config.yaml
# 的 memory.provider).
#
# 5/19 凌晨 (BL-CATFISH-MEMORY-PLUGIN-INSTALL-SCRIPT). 跟
# install-catfish-autocompress.sh 同模式.
#
# ─────────────────────────────────────────────────────────────────────
# 8/20: 改装到树外. 原来那行注释写的是"通过软链方式(避免 hermes 升级时覆盖
# plugin)" —— 意图从一开始就是对的, 但链建在了
# `~/.hermes/hermes-agent/plugins/memory/` 里, 而**升级换的正是整棵
# hermes-agent 树**. 软链跟着树一起没.
#
# 更糟的是失败形状是静默的. 升级后:
#
#     config.yaml 里 memory.provider: catfish-memory   ← 还在
#     软链                                              ← 没了
#     plugins/memory/__init__.py:201
#         """Returns None if the provider is not found or fails to load."""
#         logger.warning("Failed to load memory provider '%s': %s", name, e)
#         return None
#
# 一条 warning, 然后返 None —— 记忆静默停止工作, agent 照常回答, 只是不记事了.
#
# 对照组: `catfish-xcatfish-user` 链在 `~/.hermes/plugins/` (树外), 而且
# upgrade-hermes-v019.sh / v020.sh 两个升级脚本里都点了名. catfish-memory
# 一个都没有 —— 只在本脚本和日志脚本里出现过.
#
# 治本而不是往升级脚本里加一行的理由: 上游本来就支持树外的记忆 provider.
#   · hermes_cli/plugins.py 扫三个根: bundled / user(~/.hermes/plugins) / project
#   · 用户装的记忆 provider 会被 auto-coerce 成 kind="exclusive" (plugins.py:1613)
#   · plugins/memory/__init__.py:_iter_provider_dirs() 第 2 步就是扫 user 目录
#   · 判据 _is_memory_provider_dir(): __init__.py **前 8192 字节**里出现
#     `register_memory_provider` 或 `MemoryProvider`
#     (catfish-memory 实测在第 339 / 29 字节, 余量充足 —— 但这条判据有个
#      静默失效的边: 标记被挤出 8192 就不再被发现. 有测试钉着, 见
#      catfish-memory/tests/test_plugin_survives_hermes_upgrade.py)
#
# 放树外之后, 升级脚本不需要知道它 —— 少一处"必须记得同步"的地方.
# ─────────────────────────────────────────────────────────────────────

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
# 我们不再往 plugins/memory/ 里装东西, 但它在不在仍然是"hermes 够不够新"的
# 廉价判据 (0.13+ 才有 memory provider 体系). 留着当版本检查用.
if [ ! -d "$HERMES_AGENT_DIR/plugins/memory" ]; then
    err "$HERMES_AGENT_DIR/plugins/memory 不存在 — Hermes 版本太老 (需要 0.13+ memory provider 体系)"
    exit 1
fi
ok "Hermes agent: $HERMES_AGENT_DIR"
ok "memory provider 体系存在 (0.13+)"

# 2. 软链 catfish-memory (树外)
step 2 "软链 catfish-memory 到 ~/.hermes/plugins/ (树外)"
SRC="$SCRIPT_DIR/catfish-memory"
USER_PLUGINS="$HOME/.hermes/plugins"
DST="$USER_PLUGINS/catfish-memory"
LEGACY_DST="$HERMES_AGENT_DIR/plugins/memory/catfish-memory"

if [ ! -d "$SRC" ]; then
    err "$SRC 不存在"
    exit 1
fi

mkdir -p "$USER_PLUGINS"

# 幂等: 旧的软链或目录先清掉
if [ -L "$DST" ] || [ -e "$DST" ]; then
    rm -rf "$DST"
fi
ln -s "$SRC" "$DST"
ok "$DST -> $SRC"

# 迁移: 树内那条老链必须删掉, 不能留着"以防万一".
#
# _iter_provider_dirs() 是 bundled 优先 (第 1 步 seen.add, 第 2 步 `if child.name
# in seen: continue`). 两条链并存的话, 生效的仍然是树内那条 —— 新链一行代码都
# 跑不到, 而这里会打印"OK 已装到树外", 看上去完全正常.
#
# 那就又是一次「不会失败, 也不会生效」: 装好了, 也确实有个链在树外, 但真正在用
# 的还是会被升级抹掉的那条, 直到升级当天才发现。
if [ -L "$LEGACY_DST" ] || [ -e "$LEGACY_DST" ]; then
    rm -rf "$LEGACY_DST"
    ok "已清掉树内旧链 $LEGACY_DST (它会被 hermes 升级抹掉, 且并存时会盖住新链)"
fi

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

# catfish-memory 也注册一个名为 memory 的工具来替换 Hermes builtin。
# 只设置 memory.provider 不会授予工具覆盖权限，Hermes 会每次启动打印
# "cannot override built-in tool 'memory'"，并退回 builtin memory。
plugins = data.get("plugins") or {}
if not isinstance(plugins, dict):
    plugins = {}
entries = plugins.get("entries") or {}
if not isinstance(entries, dict):
    entries = {}
memory_plugin = entries.get("catfish-memory") or {}
if not isinstance(memory_plugin, dict):
    memory_plugin = {}
memory_plugin["allow_tool_override"] = True
entries["catfish-memory"] = memory_plugin
plugins["entries"] = entries
data["plugins"] = plugins

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
