#!/usr/bin/env bash
# mint-hermes-service-token.sh — 给 hermes-cli 拿 30 天长效 service token,
# 写到 ~/.hermes/config.yaml 的 api_key.
#
# 5/18 BL-HERMES-AUTH-LONGLIVED.
#
# 背景: hermes daemon 用 user access_token (1h TTL) 跑批撞过期, 每小时要重新
# 拉 token. 单机部署没人值守. catfish-identity 现在支持 client_credentials grant
# (RFC 6749 §4.4) + per-client TTL — hermes-cli 在 clients.yaml 配
# service_token_ttl_seconds=2592000 (30天), mint 一次能用一个月.
#
# 用法:
#   scripts/mint-hermes-service-token.sh                 # 默认 localhost:8998
#   scripts/mint-hermes-service-token.sh --dry-run       # 拉 token 但不改 config
#   IDENTITY_URL=http://10.10.40.50:8998 \
#     CLIENT_SECRET=xxx scripts/mint-hermes-service-token.sh
#
# 环境变量:
#   IDENTITY_URL    — catfish-identity 根 URL (默认 http://localhost:8998)
#   CLIENT_ID       — OAuth client_id (默认 hermes-cli)
#   CLIENT_SECRET   — OAuth client_secret 明文 (无默认, 必填)
#   HERMES_CONFIG   — hermes config.yaml 路径 (默认 ~/.hermes/config.yaml)
#
# 前置:
#   - catfish-identity 跑着, /token 可达
#   - clients.yaml 里有 hermes-cli + 正确 bcrypt hash
#   - jq + curl 可用
#
# 退出码:
#   0  成功
#   1  参数错误
#   2  token 端点失败
#   3  config 落盘失败

set -euo pipefail

IDENTITY_URL="${IDENTITY_URL:-http://localhost:8998}"
CLIENT_ID="${CLIENT_ID:-hermes-cli}"
HERMES_CONFIG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数: $arg" >&2
            exit 1
            ;;
    esac
done

if [[ -z "${CLIENT_SECRET:-}" ]]; then
    # 交互式问一次 (不写历史, 不回显)
    read -r -s -p "Hermes client_secret (明文, 不会显示也不存历史): " CLIENT_SECRET
    echo
    if [[ -z "$CLIENT_SECRET" ]]; then
        echo "必须提供 CLIENT_SECRET (env 或交互输入)" >&2
        exit 1
    fi
fi

for cmd in jq curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "缺命令: $cmd. brew install $cmd / apt install $cmd" >&2
        exit 1
    fi
done

echo "▶ 向 $IDENTITY_URL/token mint service token (client_id=$CLIENT_ID)..."

RESP="$(curl -sS -X POST "$IDENTITY_URL/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=client_credentials" \
    --data-urlencode "client_id=$CLIENT_ID" \
    --data-urlencode "client_secret=$CLIENT_SECRET" \
    || true)"

if [[ -z "$RESP" ]]; then
    echo "✗ /token 没响应 (网络不通 / identity-server 没跑?)" >&2
    exit 2
fi

if ! echo "$RESP" | jq -e '.access_token' >/dev/null 2>&1; then
    echo "✗ /token 失败:"
    echo "$RESP" | jq . 2>/dev/null || echo "$RESP"
    exit 2
fi

ACCESS_TOKEN="$(echo "$RESP" | jq -r '.access_token')"
EXPIRES_IN="$(echo "$RESP" | jq -r '.expires_in')"
SCOPE="$(echo "$RESP" | jq -r '.scope // ""')"

# 算到期日 (本地时区)
if date -d "+${EXPIRES_IN} seconds" >/dev/null 2>&1; then
    EXPIRES_AT="$(date -d "+${EXPIRES_IN} seconds" '+%Y-%m-%d %H:%M:%S')"
else
    # macOS date
    EXPIRES_AT="$(date -v "+${EXPIRES_IN}S" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo unknown)"
fi

echo "✓ token OK"
echo "   expires_in : ${EXPIRES_IN}s ($(echo "$EXPIRES_IN / 86400" | bc) 天)"
echo "   expires_at : $EXPIRES_AT"
echo "   scope      : $SCOPE"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "(--dry-run, 不改 $HERMES_CONFIG)"
    echo "access_token: $ACCESS_TOKEN"
    exit 0
fi

if [[ ! -f "$HERMES_CONFIG" ]]; then
    echo "✗ hermes config 不存在: $HERMES_CONFIG" >&2
    echo "  跑过 hermes 一次让它生成默认 config 再来" >&2
    exit 3
fi

# 备份
BACKUP="$HERMES_CONFIG.bak.$(date +%Y%m%d-%H%M%S)"
cp "$HERMES_CONFIG" "$BACKUP"
echo "  备份: $BACKUP"

# 替换 api_key. hermes config 里 api_key 可能出现在多处:
#   - providers.catfish-gateway.api_key
#   - custom_providers.<name>.api_key
# 用 python (大概率装了) 改 yaml 比 sed 安全.
python3 - "$HERMES_CONFIG" "$ACCESS_TOKEN" <<'PY'
import sys, pathlib
try:
    import yaml
except ImportError:
    sys.stderr.write("缺 PyYAML — pip3 install --user pyyaml\n")
    sys.exit(3)

cfg_path = pathlib.Path(sys.argv[1])
new_key = sys.argv[2]
data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

# 收集所有需要改 api_key 的位置 (catfish-gateway 走的, 不动 OpenAI/Anthropic key)
def walk_provider(node, label):
    if not isinstance(node, dict):
        return
    for name, p in list(node.items()):
        if not isinstance(p, dict):
            continue
        url = (p.get("base_url") or p.get("url") or "").lower()
        if "catfish" in name.lower() or "catfish" in url or "10.10.40.50" in url or "localhost:8999" in url:
            old = p.get("api_key", "")[:8] + "..." if p.get("api_key") else "(empty)"
            p["api_key"] = new_key
            print(f"  ✓ {label}.{name}: {old} → {new_key[:8]}...")

walk_provider(data.get("providers"), "providers")
walk_provider(data.get("custom_providers"), "custom_providers")

cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"  ✓ 写入 {cfg_path}")
PY

echo
echo "✓ hermes 配置已更新. 重启 hermes daemon 让新 token 生效:"
echo "    pkill -f 'hermes serve' 2>/dev/null; nohup hermes serve > ~/.hermes/serve.log 2>&1 &"
echo
echo "到期前重跑这个脚本续 token. 也可加 cron 每 25 天自动续:"
echo "    0 3 1 * * CLIENT_SECRET=xxx $(realpath "$0") >> ~/.hermes/mint.log 2>&1"
