#!/usr/bin/env bash
# =============================================================
# 鲶鱼 Catfish · 员工一键装
# =============================================================
#
# 装完后这个员工就有了：
#   - catfish-search          本地文件全文搜索 CLI
#   - catfish-search daemon   后台增量索引（可选，默认装）
#   - Hermes MCP server       让 Hermes 自动调本地搜索
#
# 不装 gateway（那是公司集中部署的，员工只需要在 Hermes 里配 URL）。
#
# 使用：
#   bash onboarding/install-catfish.sh            # 交互式
#   bash onboarding/install-catfish.sh --yes      # 全默认，IT 批量部署用
#   bash onboarding/install-catfish.sh --skip-index   # 不触发首次 index（晚点再手动跑）
#
# 幂等：重复执行跳过已完成步骤，不会搞坏东西。
# =============================================================

set -euo pipefail

# ---------- 颜色 / 打印 ----------

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

step() { echo -e "${BOLD}[$1/$TOTAL]${RESET} $2"; }
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }
err()  { echo -e "    ${RED}错误${RESET} $*" >&2; }

# ---------- 参数 ----------

AUTO_YES=0
SKIP_INDEX=0
SKIP_DAEMON=0
SKIP_BROWSER_ATTACH=0

for arg in "$@"; do
    case "$arg" in
        -y|--yes)                 AUTO_YES=1 ;;
        --skip-index)             SKIP_INDEX=1 ;;
        --skip-daemon)            SKIP_DAEMON=1 ;;
        --skip-browser-attach)    SKIP_BROWSER_ATTACH=1 ;;
        -h|--help)
            sed -n '2,21p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            err "未知参数：$arg（用 --help 看说明）"
            exit 2
            ;;
    esac
done

confirm() {
    # $1 = 提示, $2 = 默认 (Y/n)
    if [ "$AUTO_YES" = "1" ]; then return 0; fi
    local prompt="$1"
    local default="${2:-Y}"
    local reply
    if [ "$default" = "Y" ]; then
        read -r -p "    $prompt [Y/n] " reply
        [ -z "$reply" ] || [[ "$reply" =~ ^[Yy] ]]
    else
        read -r -p "    $prompt [y/N] " reply
        [[ "$reply" =~ ^[Yy] ]]
    fi
}

# ---------- 0. Preflight ----------

TOTAL=9
echo -e "${BOLD}鲶鱼 Catfish 员工工具一键装${RESET}"
echo

step 1 "检查前置条件"

# 找 Python 3.10+（优先新版本）
PYTHON_BIN=""
for bin in python3.12 python3.11 python3.10; do
    if command -v "$bin" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$bin")"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    # 退到 python3，看版本够不够
    if command -v python3 >/dev/null 2>&1; then
        ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -gt 3 ] || { [ "$major" = "3" ] && [ "$minor" -ge 10 ]; }; then
            PYTHON_BIN="$(command -v python3)"
        fi
    fi
fi
if [ -z "$PYTHON_BIN" ]; then
    err "找不到 Python 3.10+"
    echo "    安装方式："
    echo "        macOS:  brew install python@3.12"
    echo "        Ubuntu: sudo apt install python3.12"
    exit 1
fi
PY_VER=$("$PYTHON_BIN" --version | awk '{print $2}')
ok "Python $PY_VER ($PYTHON_BIN)"

# 定位 catfish 项目根
CATFISH_ROOT=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../catfish-design.md" ]; then
    CATFISH_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -f "$PWD/catfish-design.md" ]; then
    CATFISH_ROOT="$PWD"
fi
if [ -z "$CATFISH_ROOT" ]; then
    err "找不到 catfish 项目根（需要包含 catfish-design.md 的目录）"
    echo "    解决：把项目 clone 到员工机器，然后在项目根下跑这个脚本。"
    exit 1
fi
ok "catfish 项目根：$CATFISH_ROOT"

# Hermes 装没装（可选，没装也能用 catfish-search CLI）
HAS_HERMES=0
if command -v hermes >/dev/null 2>&1; then
    HAS_HERMES=1
    ok "Hermes Agent $(hermes --version 2>/dev/null | head -1 || echo installed)"
else
    warn "Hermes Agent 未安装 —— 跳过 MCP 注册，只装 catfish-search CLI"
fi

# ---------- 2. 建员工 venv ----------

step 2 "准备员工 venv ~/.catfish/venv"

CATFISH_HOME="$HOME/.catfish"
CATFISH_VENV="$CATFISH_HOME/venv"
mkdir -p "$CATFISH_HOME"

if [ ! -d "$CATFISH_VENV" ]; then
    "$PYTHON_BIN" -m venv "$CATFISH_VENV"
    ok "创建 $CATFISH_VENV"
else
    # 确认 venv 还活着（python binary 没被 brew 升级成指 broken path）
    if ! "$CATFISH_VENV/bin/python" --version >/dev/null 2>&1; then
        warn "venv 损坏（Python 可能被系统升级带坏），重建"
        rm -rf "$CATFISH_VENV"
        "$PYTHON_BIN" -m venv "$CATFISH_VENV"
    fi
    ok "复用已有 venv"
fi

VENV_PY="$CATFISH_VENV/bin/python"
VENV_PIP="$CATFISH_VENV/bin/pip"

# ---------- 3. pip install ----------

step 3 "装 catfish-local-search + 依赖"

"$VENV_PIP" install -q -U pip
LOCAL_SEARCH_DIR="$CATFISH_ROOT/edge/local-search"
if [ ! -f "$LOCAL_SEARCH_DIR/pyproject.toml" ]; then
    err "$LOCAL_SEARCH_DIR/pyproject.toml 不存在（项目结构问题）"
    exit 1
fi

# editable install，方便以后 pull 代码就能用新版本
"$VENV_PIP" install -q -e "$LOCAL_SEARCH_DIR[all,mcp]" 2>&1 | tail -5 || {
    err "pip install 失败，检查网络或公司内网 PyPI mirror 配置"
    exit 1
}
ok "catfish-search, catfish-search-mcp 装好"

# ---------- 4. 软链到 PATH ----------

step 4 "软链命令到 ~/.local/bin"

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
for cmd in catfish-search catfish-search-mcp; do
    SRC="$CATFISH_VENV/bin/$cmd"
    DST="$LOCAL_BIN/$cmd"
    if [ -x "$SRC" ]; then
        ln -sf "$SRC" "$DST"
        ok "$DST -> $SRC"
    else
        warn "$SRC 不存在，跳过"
    fi
done

# 提醒 PATH
case ":$PATH:" in
    *":$LOCAL_BIN:"*) ;;
    *)
        warn "~/.local/bin 不在 PATH 里，加到 shell rc："
        echo "        echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
        echo "        source ~/.zshrc"
        ;;
esac

# ---------- 5. 生成默认配置 ----------

step 5 "初始化 ~/.catfish/search-scope.yaml"

SCOPE_FILE="$CATFISH_HOME/search-scope.yaml"
if [ -f "$SCOPE_FILE" ]; then
    ok "已存在，保留员工自定义（如要重置：删掉这个文件再跑 catfish-search index）"
else
    # 跑一次 catfish-search status 会触发默认配置生成
    "$CATFISH_VENV/bin/catfish-search" status >/dev/null 2>&1 || true
    if [ -f "$SCOPE_FILE" ]; then
        ok "已生成默认 include：~/Documents, ~/Desktop, ~/Downloads"
        ok "以后要扩范围就改：$SCOPE_FILE"
    else
        warn "配置生成失败（catfish-search 调不通），请手动跑 catfish-search config"
    fi
fi

# ---------- 6. 首次 index ----------

step 6 "首次全量索引"

if [ "$SKIP_INDEX" = "1" ]; then
    ok "跳过（--skip-index）。以后手动跑 catfish-search index"
elif confirm "现在跑一次首次全量索引吗？（视文件多少，可能 1~10 分钟）" Y; then
    "$CATFISH_VENV/bin/catfish-search" index || warn "index 失败，晚点手动重试"
    ok "索引完成。运行 catfish-search status 查看统计"
else
    ok "跳过，以后手动跑 catfish-search index"
fi

# ---------- 7. 装 Hermes MCP ----------

step 7 "装 Hermes MCP server 和 skill 文档"

if [ "$HAS_HERMES" = "0" ]; then
    ok "跳过（Hermes 未装）"
else
    HERMES_INSTALL_SH="$LOCAL_SEARCH_DIR/hermes-skill/install.sh"
    if [ -x "$HERMES_INSTALL_SH" ]; then
        bash "$HERMES_INSTALL_SH" || warn "MCP 注册失败（看上面报错，必要时跑 hermes mcp test catfish-local-search）"
        ok "装好。记得 /exit 重启 Hermes 才生效"
    else
        warn "找不到 $HERMES_INSTALL_SH"
    fi
fi

# ---------- 8. 装 watchdog daemon（可选）----------

step 8 "后台 watcher（增量索引 + 崩溃自重启）"

if [ "$SKIP_DAEMON" = "1" ]; then
    ok "跳过（--skip-daemon）"
elif confirm "装后台 watcher 吗？文件变动会自动更新索引（macOS launchd / Linux systemd / Windows 任务计划）" Y; then
    "$CATFISH_VENV/bin/catfish-search" daemon install || warn "daemon install 失败"
    ok "装好。查状态：catfish-search daemon status"
else
    ok "跳过。以后可手动：catfish-search daemon install"
fi

# ---------- 9. Browser CDP attach（可选 · 隐私升级需要明确同意）----------

step 9 "Browser Agent · CDP attach 模式（可选）"

if [ "$HAS_HERMES" = "0" ]; then
    ok "跳过（Hermes 未装，attach 没有意义）"
elif [ "$SKIP_BROWSER_ATTACH" = "1" ]; then
    ok "跳过（--skip-browser-attach）"
else
    cat <<'PRIVACY_NOTE'

    这个模式让 Hermes 用一个**专用 Chrome profile**（不是你日常 Chrome）：
      优点：browser_navigate 从 30~60 秒变 <3 秒
      隔离：Hermes 只看得到这个专用 profile，你日常 Chrome 的 tab / 登录态完全不被看到
      代价：要 Hermes 访问公司内网，要在这个专用 Chrome 里手动登一次（登录态会持久化）

    Chrome 从 2024 起禁止默认 profile 下开 debug port，所以必须独立 profile。
    这反而让"Hermes Chrome"和"员工 Chrome"物理隔离，隐私更安全。

    不想现在就定，跳过也没事。以后需要时跑：
      bash edge/browser-agent/scripts/catfish-browser-attach.sh

PRIVACY_NOTE
    if confirm "现在启用 Browser CDP attach 模式吗？" N; then
        ATTACH_SCRIPT="$LOCAL_SEARCH_DIR/../browser-agent/scripts/catfish-browser-attach.sh"
        if [ -x "$ATTACH_SCRIPT" ]; then
            bash "$ATTACH_SCRIPT" || warn "attach 脚本执行出错（不影响别的功能）"
        else
            warn "没找到 $ATTACH_SCRIPT，跳过"
        fi
    else
        ok "跳过。默认保持 local Chromium 模式（首次 navigate 30~60s，但员工 Chrome 纯私人）"
    fi
fi

# ---------- 收尾 ----------

echo
echo -e "${BOLD}${GREEN}=== 装好了 ===${RESET}"
cat <<EOF

日常使用：

  catfish-search query "合同"            # 搜文件
  catfish-search status                   # 看索引库状态
  catfish-search config                   # 改索引范围

在 Hermes 里直接问："帮我找我电脑里关于 X 的文档"，小鲶会自动调本地搜索。

如果需要连公司的 LLM gateway（问 IT 要 URL 和 token）：
  hermes /model 选 Custom endpoint 填进去即可。

卸载：
  $LOCAL_SEARCH_DIR/hermes-skill/uninstall.sh                 # 卸 Hermes MCP
  catfish-search daemon uninstall                              # 卸后台 watcher
  $LOCAL_SEARCH_DIR/../browser-agent/scripts/catfish-browser-detach.sh  # 卸 browser attach
  rm -rf ~/.catfish/                                           # 删索引库和配置
EOF
