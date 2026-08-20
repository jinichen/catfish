#!/usr/bin/env bash
# setup-catfish-edge.sh — 员工 Mac 一键装/配 catfish-edge 全套.
#
# 5/19 BL-SETUP-CATFISH-EDGE-SCRIPT (BL-MEMORY-OWNERSHIP-FIX Phase 2 配套).
#
# 这个脚本:
#   1. 生成强随机 API_SERVER_KEY (hermes ↔ Companion 共享密钥)
#   2. 写 ~/.hermes/.env (API_SERVER_ENABLED=true + API_SERVER_KEY=...)
#   3. 写 ~/.catfish/companion.yaml hermes_api 段 (enabled + url + key)
#   4. 软链 catfish-memory plugin 到 ~/.hermes/plugins/ (**树外**, 8/20 改) +
#      改 hermes config memory.provider = catfish-memory
#      (调 install-catfish-memory.sh -y)
#      树外的原因: 升级换的是整棵 hermes-agent/, 装在树里的软链会静默消失,
#      而 provider 加载失败只打一条 warning 就返 None。详见那个脚本的头注释。
#   5. (可选) 软链 catfish-autocompress plugin (调 install-catfish-autocompress.sh)
#   6. (可选) 重启 hermes gateway 让新 .env 生效
#
# 前置: hermes 已装 (~/.hermes/hermes-agent/), Companion 已装 (Tauri .app).
# 不装 hermes / Companion 本身, 那是独立 installer 的事.
#
# 用法:
#   bash scripts/setup-catfish-edge.sh           # 交互式
#   bash scripts/setup-catfish-edge.sh -y         # 全部 yes (CI / 批量装)
#   bash scripts/setup-catfish-edge.sh --rotate   # 只 rotate API_SERVER_KEY
#                                                 # (季度安全维护用)
#
# Key rotation:
#   季度跑 `bash setup-catfish-edge.sh --rotate` 换新 key, 详见
#   docs/COMPANION-HERMES-AUTH-DESIGN.md §2.3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATFISH_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_AGENT_DIR="$HOME/.hermes/hermes-agent"
HERMES_ENV="$HOME/.hermes/.env"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
COMPANION_YAML="$HOME/.catfish/companion.yaml"
HERMES_API_URL="http://localhost:8642"

BOLD='\033[1m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'
step() { echo -e "${BOLD}[$1/$TOTAL]${RESET} $2"; }
ok()   { echo -e "    ${GREEN}OK${RESET} $*"; }
warn() { echo -e "    ${YELLOW}警告${RESET} $*"; }
err()  { echo -e "    ${RED}错误${RESET} $*" >&2; }

AUTO_YES=0
ROTATE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes)   AUTO_YES=1 ;;
        --rotate)   ROTATE_ONLY=1 ;;
        -h|--help)
            sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
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

# 选 python: hermes venv 优先 (含 pyyaml)
choose_python() {
    if [ -x "$HERMES_AGENT_DIR/venv/bin/python" ]; then
        echo "$HERMES_AGENT_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        echo "python3"
    else
        err "找不到 python3 / hermes venv"
        exit 1
    fi
}
PY="$(choose_python)"

# 生成强随机 token (64 hex)
gen_key() {
    "$PY" -c "import secrets; print(secrets.token_hex(32))"
}

TOTAL=7  # P3.5.48: 加 step 3 token + secret 持久化
[ "$ROTATE_ONLY" = "1" ] && TOTAL=4 && echo -e "${BOLD}🔄 rotate-only 模式 — 只换 key${RESET}\n"

# ===========================================================================
# Step 1. 前置检查
# ===========================================================================
step 1 "前置检查"
if [ ! -d "$HERMES_AGENT_DIR" ]; then
    err "找不到 $HERMES_AGENT_DIR. 先装 hermes (curl -fsSL get.hermes.fm | bash)."
    exit 1
fi
mkdir -p "$HOME/.hermes" "$HOME/.catfish"
touch "$HERMES_ENV"
touch "$COMPANION_YAML"
ok "目录就绪: ~/.hermes ~/.catfish"
ok "hermes-agent: $HERMES_AGENT_DIR"

# ===========================================================================
# Step 2. 生成 / rotate API_SERVER_KEY
# ===========================================================================
step 2 "生成 API_SERVER_KEY"
KEY="$(gen_key)"
ok "新 key (64 hex): ${KEY:0:16}..."

# 备份原 .env (如果有 old key)
if grep -q "^API_SERVER_KEY=" "$HERMES_ENV" 2>/dev/null; then
    cp "$HERMES_ENV" "$HERMES_ENV.bak.$(date +%Y%m%d-%H%M%S)"
    OLD_KEY="$(grep '^API_SERVER_KEY=' "$HERMES_ENV" | cut -d= -f2 | head -1)"
    warn "rotate: old key ${OLD_KEY:0:16}... → new ${KEY:0:16}..."
fi

# 写 ~/.hermes/.env
# 用 python 改 .env 安全 (不破坏其它行)
"$PY" - "$HERMES_ENV" "$KEY" <<'PYEOF'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
new_key = sys.argv[2]

lines = env_path.read_text("utf-8").splitlines() if env_path.exists() else []
out = []
saw_enabled = False
saw_key = False
for line in lines:
    if line.startswith("API_SERVER_ENABLED="):
        out.append("API_SERVER_ENABLED=true")
        saw_enabled = True
    elif line.startswith("API_SERVER_KEY="):
        out.append(f"API_SERVER_KEY={new_key}")
        saw_key = True
    else:
        out.append(line)
if not saw_enabled:
    out.append("API_SERVER_ENABLED=true")
if not saw_key:
    out.append(f"API_SERVER_KEY={new_key}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PYEOF
chmod 600 "$HERMES_ENV"
ok "写 $HERMES_ENV (chmod 600)"

# ===========================================================================
# Step 3 (P3.5.48). HERMES_SERVICE_TOKEN + CATFISH_HERMES_CLIENT_SECRET
#                   ── 一次性配 + 让 plugin 永续 auto-renew
# ===========================================================================
# 真因 (鸿波 6/21 catch '为啥自动续期没起来'):
#   老路径: 装机只写 API_SERVER_KEY, HERMES_SERVICE_TOKEN 让员工"用 mint script
#   拿". 鸿波装机后手动 mint 过一次, 30 天后过期没人续 → 所有走 P7 转发
#   gateway 的 /api/* 全 401. P3.5.44 加了 plugin auto-renew, 但 secret env
#   name 不一致 (CATFISH_HERMES_CLIENT_SECRET vs CLIENT_SECRET) silent fail.
#
# 治本: setup 时检测 .env 缺 HERMES_SERVICE_TOKEN / 缺 secret 就交互式收 +
# 调 mint --persist-secret 一次. 写盘后 plugin auto-renew 永远 work, 不再
# 依赖手动 cron.
# ===========================================================================
step 3 "HERMES_SERVICE_TOKEN + 自动续期 secret (BL-TOKEN-AUTO-RENEW)"

# 检测 .env 现状
HAVE_TOKEN=0
HAVE_SECRET=0
if grep -qE "^(export\s+)?HERMES_SERVICE_TOKEN=" "$HERMES_ENV" 2>/dev/null; then HAVE_TOKEN=1; fi
if grep -qE "^(export\s+)?(CATFISH_HERMES_CLIENT_SECRET|CLIENT_SECRET)=" "$HERMES_ENV" 2>/dev/null; then HAVE_SECRET=1; fi

if [ "$HAVE_TOKEN" = "1" ] && [ "$HAVE_SECRET" = "1" ]; then
    ok "HERMES_SERVICE_TOKEN + auto-renew secret 都已配, 跳过"
else
    [ "$HAVE_TOKEN" = "0" ] && warn "~/.hermes/.env 缺 HERMES_SERVICE_TOKEN"
    [ "$HAVE_SECRET" = "0" ] && warn "~/.hermes/.env 缺 CATFISH_HERMES_CLIENT_SECRET (plugin auto-renew 用)"
    MINT_SH="$SCRIPT_DIR/mint-hermes-service-token.sh"
    if [ ! -x "$MINT_SH" ]; then
        warn "找不到 $MINT_SH, 跳过 (老员工自己手动 mint)"
    elif ! confirm "立刻调 mint-hermes-service-token.sh --persist-secret 配上 (一次性, 之后 plugin 永续自动续期)?"; then
        warn "跳过 token 配置 — 30 天周期手动续, 或重跑本 script"
    else
        # client_secret 通过 CLIENT_SECRET env 或 mint script 交互 prompt 收
        # P3.5.50: 鸿波直接 Enter 时给 demo secret 兜底 (dev / 单机部署常用,
        # 生产部署 IT 会改 clients.yaml hash). 真生产装机请把 CLIENT_SECRET
        # 通过 env 传 (避免 demo secret 进 .env).
        DEMO_SECRET="hermes-dev-secret-2026-please-change"
        if [ -z "${CLIENT_SECRET:-}" ]; then
            echo "    catfish-identity clients.yaml hermes-cli secret"
            echo "    (直接 Enter 用 demo: $DEMO_SECRET — 生产请先 export CLIENT_SECRET=<真secret> 再跑本脚本)"
            read -r -s -p "    CLIENT_SECRET (不回显, Enter=demo): " CLIENT_SECRET
            echo
            [ -z "$CLIENT_SECRET" ] && CLIENT_SECRET="$DEMO_SECRET" && warn "用 demo secret (dev/单机 OK, 生产需换)"
            export CLIENT_SECRET
        fi
        if [ -z "$CLIENT_SECRET" ]; then
            err "CLIENT_SECRET 空, 跳过"
        elif bash "$MINT_SH" --persist-secret; then
            ok "token + secret 都写 ~/.hermes/.env, plugin auto-renew 接管"
        else
            err "mint 失败 — 看上面 log (identity-server 没跑? secret 错?)"
        fi
        unset CLIENT_SECRET  # 不留到后续 step
    fi
fi

# ===========================================================================
# Step 4. 写 ~/.catfish/companion.yaml hermes_api 段
# ===========================================================================
step 4 "写 ~/.catfish/companion.yaml hermes_api"
"$PY" - "$COMPANION_YAML" "$HERMES_API_URL" "$KEY" <<'PYEOF'
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    sys.stderr.write("    需要 pyyaml — pip3 install --user pyyaml\n")
    sys.exit(1)

cfg_path = Path(sys.argv[1])
url = sys.argv[2]
key = sys.argv[3]

data = yaml.safe_load(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}
if not isinstance(data, dict):
    data = {}

# 备份
if cfg_path.exists():
    backup = cfg_path.with_suffix(cfg_path.suffix + ".bak.installer")
    backup.write_text(cfg_path.read_text("utf-8"), "utf-8")

data.setdefault("hermes_api", {}).update({
    # BL-AUTH-DECOUPLE-A5 (5/19) + BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1):
    # enabled=true 让 Companion 走 hermes 8642 + API_SERVER_KEY 认证.
    # hermes_cli /v1/chat/completions 用内部 LiteLLM (model.api_key = service token).
    # hermes catch-all /api/* 走 catfish-xcatfish-user plugin P7,
    # _handle_companion_proxy 替换 Authorization 成 HERMES_SERVICE_TOKEN
    # 转发 gateway, X-Catfish-User 透传 user identity.
    #
    # 必需 env (~/.hermes/.env, 本脚本 step 2 写):
    #   API_SERVER_ENABLED=true        — hermes 起 8642 API server
    #   API_SERVER_KEY=<64hex>          — Companion 认证用
    #   HERMES_SERVICE_TOKEN=<JWT>     — plugin P7 替换转发 gateway 用
    #                                    (mint via mint-hermes-service-token.sh)
    "enabled": True,
    "url": url,
    "key": key,
})

cfg_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PYEOF
chmod 600 "$COMPANION_YAML"
ok "写 $COMPANION_YAML (chmod 600, hermes_api 段已 enabled)"

[ "$ROTATE_ONLY" = "1" ] && {
    # rotate-only 跳过 step 3 token (没换 client_secret 跟 token 周期)
    step 4 "重启 hermes gateway"
    if confirm "重启 hermes gateway 让新 key 生效?"; then
        if command -v hermes >/dev/null 2>&1; then
            hermes gateway restart 2>&1 | tail -3 || warn "hermes gateway restart 失败, 你可手动重启"
        else
            warn "找不到 hermes 命令, 自己重启"
        fi
        ok "(你也需要重启 Companion 让它读新 yaml)"
    fi
    echo
    ok "${GREEN}rotate 完成${RESET}"
    exit 0
}

# ===========================================================================
# Step 5. 装 catfish-memory plugin (调子脚本)
# ===========================================================================
step 5 "装 catfish-memory hermes plugin"
PLUGIN_INSTALL_SH="$CATFISH_REPO/edge/hermes-plugins/install-catfish-memory.sh"
if [ -x "$PLUGIN_INSTALL_SH" ]; then
    if confirm "调 install-catfish-memory.sh 装 plugin + 激活?"; then
        bash "$PLUGIN_INSTALL_SH" -y || warn "install-catfish-memory.sh 报错, 看上面 log"
    else
        ok "跳过 catfish-memory plugin (你自己装)"
    fi
else
    warn "找不到 $PLUGIN_INSTALL_SH, 跳过"
fi

# ===========================================================================
# Step 6. (可选) 装 catfish-autocompress plugin
# ===========================================================================
step 6 "(可选) 装 catfish-autocompress hermes plugin"
AC_INSTALL_SH="$CATFISH_REPO/edge/hermes-plugins/install.sh"
if [ -x "$AC_INSTALL_SH" ]; then
    if confirm "调 install.sh 装 catfish-autocompress?"; then
        bash "$AC_INSTALL_SH" -y || warn "install.sh 报错, 看上面 log"
    else
        ok "跳过 catfish-autocompress"
    fi
else
    warn "找不到 $AC_INSTALL_SH, 跳过"
fi

# ===========================================================================
# Step 7. 重启 hermes gateway
# ===========================================================================
step 7 "重启 hermes gateway"
if confirm "重启 hermes gateway 让所有配置生效?"; then
    if command -v hermes >/dev/null 2>&1; then
        hermes gateway restart 2>&1 | tail -5 || warn "hermes gateway restart 失败"
        # P3.5.50: v0.17 启动慢 (god-file refactor + 多 mixin import + plugin
        # discover scan), 老 sleep 3 经常没等到 bind. 改循环 poll 最多 30s,
        # bind 上立刻返, 真没起来就给详细诊断.
        echo "    等 hermes bind 8642 (最多 30s)..."
        bound=0
        for i in $(seq 1 30); do
            if lsof -i :8642 >/dev/null 2>&1; then
                ok "hermes API server 监听 :8642 ✓ (${i}s)"
                bound=1
                break
            fi
            sleep 1
        done
        if [ "$bound" = "0" ]; then
            warn "30s 内 8642 没监听 — hermes 可能起来失败 / plugin import error 卡死"
            echo "    诊断:"
            echo "      tail -50 ~/.hermes/logs/gateway.error.log | grep -iE 'error|traceback|circular'"
            echo "      pgrep -fl hermes-agent  # 看进程在不在"
        fi
    else
        warn "找不到 hermes 命令"
    fi
fi

echo
echo -e "${BOLD}${GREEN}✓ catfish-edge 安装完成${RESET}"
echo
echo -e "${BOLD}${YELLOW}⚠️  下一步必须做 (key 真 rotate 了, Companion 不重启会用老 key 401):${RESET}"
echo "  1. 完全退出 Companion: Dock 右键鲶鱼 Companion → 退出 (或 Cmd+Q **必须**确认对话框点退出, 关窗口 ✗ 没用)"
echo "  2. 重新打开 Companion"
echo "  3. 验真: 跟小鲶聊 '执行 ls' 看 execute_code 真返结果"
echo
echo "其他验证:"
echo "  hermes memory status              # 应显 'catfish-memory ← active'"
echo "  curl -H 'Authorization: Bearer \$KEY' http://localhost:8642/v1/models"
echo
echo "Key rotation (季度跑一次):"
echo "  bash $0 --rotate"
echo
echo "详见:"
echo "  - docs/MEMORY-OWNERSHIP-ARCHITECTURE.md"
echo "  - docs/COMPANION-HERMES-AUTH-DESIGN.md"
echo "  - docs/HERMES-OPENAI-SERVER-RESEARCH.md"
