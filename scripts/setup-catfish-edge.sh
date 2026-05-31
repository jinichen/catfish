#!/usr/bin/env bash
# setup-catfish-edge.sh — 员工 Mac 一键装/配 catfish-edge 全套.
#
# 5/19 BL-SETUP-CATFISH-EDGE-SCRIPT (BL-MEMORY-OWNERSHIP-FIX Phase 2 配套).
#
# 这个脚本:
#   1. 生成强随机 API_SERVER_KEY (hermes ↔ Companion 共享密钥)
#   2. 写 ~/.hermes/.env (API_SERVER_ENABLED=true + API_SERVER_KEY=...)
#   3. 写 ~/.catfish/companion.yaml hermes_api 段 (enabled + url + key)
#   4. 软链 catfish-memory plugin 到 hermes plugins/memory/ + 改 hermes config
#      memory.provider = catfish-memory (调 install-catfish-memory.sh -y)
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

TOTAL=6
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
# Step 3. 写 ~/.catfish/companion.yaml hermes_api 段
# ===========================================================================
step 3 "写 ~/.catfish/companion.yaml hermes_api"
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
    # BL-HERMES-PROXY-AUTH-ME (6/1): enabled=true 让 Companion 走 hermes proxy
    # 服务 chat (/v1/chat/completions) — hermes 有专门 handler 用 service token
    # 替换转发 gateway, 配 X-Catfish-User 走 BL-AUTH-DECOUPLE-A5 设计.
    #
    # 但 /api/me /api/audit/me /api/quota/me /api/proactive/* 这几条路径
    # hermes 端**没实现** token 替换, 透传 64hex API_SERVER_KEY 给 gateway,
    # gateway 期 JWT → 401. 这是 hermes 上游 bug.
    #
    # Companion 端 me.ts:fetchWithAuth 有 path 感知补救: /api/* 自动绕过
    # hermes 走 OAuth 直连 gatewayUrl. 所以 enabled=true 仍安全.
    #
    # 等 hermes 上游补 /api/* 替换后 me.ts path 感知可砍.
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
# Step 4. 装 catfish-memory plugin (调子脚本)
# ===========================================================================
step 4 "装 catfish-memory hermes plugin"
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
# Step 5. (可选) 装 catfish-autocompress plugin
# ===========================================================================
step 5 "(可选) 装 catfish-autocompress hermes plugin"
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
# Step 6. 重启 hermes gateway
# ===========================================================================
step 6 "重启 hermes gateway"
if confirm "重启 hermes gateway 让所有配置生效?"; then
    if command -v hermes >/dev/null 2>&1; then
        hermes gateway restart 2>&1 | tail -5 || warn "hermes gateway restart 失败"
        sleep 3
        if lsof -i :8642 >/dev/null 2>&1; then
            ok "hermes API server 监听 :8642 ✓"
        else
            warn "8642 没监听, 看 ~/.hermes/logs/gateway.error.log"
        fi
    else
        warn "找不到 hermes 命令"
    fi
fi

echo
echo -e "${BOLD}${GREEN}✓ catfish-edge 安装完成${RESET}"
echo
echo "验证:"
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
