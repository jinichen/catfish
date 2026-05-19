#!/usr/bin/env bash
# mint-hermes-service-token.sh — 给 hermes-cli 拿 30 天长效 service token.
#
# 5/18 BL-HERMES-AUTH-LONGLIVED.
# 5/19 BL-AUTH-DECOUPLE-A4: token 写到 ~/.hermes/.env 的 HERMES_SERVICE_TOKEN,
#       config.yaml.api_key 改成 ${HERMES_SERVICE_TOKEN} 模板,
#       hermes 启动时由 _expand_env_vars 在 load 时展开. 这样滚 token 只动
#       .env, config.yaml 模板保持稳定 (不会每次 mint 都 churn 一个新 JWT 进
#       config diff).
#
# 背景: hermes daemon 用 user access_token (1h TTL) 跑批撞过期, 每小时要重新
# 拉 token. 单机部署没人值守. catfish-identity 现在支持 client_credentials grant
# (RFC 6749 §4.4) + per-client TTL — hermes-cli 在 clients.yaml 配
# service_token_ttl_seconds=2592000 (30天), mint 一次能用一个月.
#
# 用法:
#   scripts/mint-hermes-service-token.sh                 # 默认 localhost:8998
#   scripts/mint-hermes-service-token.sh --dry-run       # 拉 token 但不改文件
#   IDENTITY_URL=http://10.10.40.50:8998 \
#     CLIENT_SECRET=xxx scripts/mint-hermes-service-token.sh
#
# 环境变量:
#   IDENTITY_URL    — catfish-identity 根 URL (默认 http://localhost:8998)
#   CLIENT_ID       — OAuth client_id (默认 hermes-cli)
#   CLIENT_SECRET   — OAuth client_secret 明文 (无默认, 必填)
#   HERMES_CONFIG   — hermes config.yaml 路径 (默认 ~/.hermes/config.yaml)
#   HERMES_ENV      — hermes .env 路径 (默认 ~/.hermes/.env)
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
#   3  文件落盘失败

set -euo pipefail

IDENTITY_URL="${IDENTITY_URL:-http://localhost:8998}"
CLIENT_ID="${CLIENT_ID:-hermes-cli}"
HERMES_CONFIG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
HERMES_ENV="${HERMES_ENV:-$HOME/.hermes/.env}"
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'
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
    echo "(--dry-run, 不改 $HERMES_ENV / $HERMES_CONFIG)"
    echo "access_token: $ACCESS_TOKEN"
    exit 0
fi

TS="$(date +%Y%m%d-%H%M%S)"

# ─── Step 1: 写到 ~/.hermes/.env 的 HERMES_SERVICE_TOKEN ───────────────────
if [[ ! -f "$HERMES_ENV" ]]; then
    # .env 不存在: 创建一个空的
    touch "$HERMES_ENV"
    chmod 600 "$HERMES_ENV"
    echo "  (新建 $HERMES_ENV)"
fi

# 备份
ENV_BACKUP="$HERMES_ENV.bak.$TS"
cp "$HERMES_ENV" "$ENV_BACKUP"
echo "  备份: $ENV_BACKUP"

# 替换 / 追加 HERMES_SERVICE_TOKEN= 行. 用 python 比 sed 安全 (token 含
# /, +, =, 各种正则元字符, sed 用 # 分隔也不一定够).
python3 - "$HERMES_ENV" "$ACCESS_TOKEN" <<'PY'
import sys, pathlib

env_path = pathlib.Path(sys.argv[1])
new_token = sys.argv[2]
key = "HERMES_SERVICE_TOKEN"

lines = env_path.read_text(encoding="utf-8").splitlines(keepends=False)
found = False
out = []
for line in lines:
    # 匹配 KEY= (允许前面可选 export, 不允许注释行)
    stripped = line.lstrip()
    if stripped.startswith("#"):
        out.append(line)
        continue
    # split key=value
    if "=" in stripped:
        k = stripped.split("=", 1)[0].strip()
        # strip "export " prefix if present
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k == key:
            out.append(f"{key}={new_token}")
            found = True
            continue
    out.append(line)

if not found:
    # 追加到末尾, 带个分节注释
    if out and out[-1] != "":
        out.append("")
    out.append("# BL-AUTH-DECOUPLE-A4: hermes-cli service token (30d).")
    out.append("# mint via scripts/mint-hermes-service-token.sh.")
    out.append(f"{key}={new_token}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"  ✓ {'更新' if found else '追加'} {key} in {env_path}")
PY

# ─── Step 2: 确保 config.yaml.model.api_key + custom_providers[*].api_key
#             是 ${HERMES_SERVICE_TOKEN} 模板 ──────────────────────────────
if [[ ! -f "$HERMES_CONFIG" ]]; then
    echo "✗ hermes config 不存在: $HERMES_CONFIG" >&2
    echo "  跑过 hermes 一次让它生成默认 config 再来" >&2
    exit 3
fi

CFG_BACKUP="$HERMES_CONFIG.bak.$TS"
cp "$HERMES_CONFIG" "$CFG_BACKUP"
echo "  备份: $CFG_BACKUP"

# 用 python 改 yaml: 把所有 catfish-gateway 相关的 api_key 改成模板.
# 注意: 不动 OpenAI / Anthropic / 其他真 API key 字段.
python3 - "$HERMES_CONFIG" <<'PY'
import sys, pathlib
try:
    import yaml
except ImportError:
    sys.stderr.write("缺 PyYAML — pip3 install --user pyyaml\n")
    sys.exit(3)

cfg_path = pathlib.Path(sys.argv[1])
TEMPLATE = "${HERMES_SERVICE_TOKEN}"

raw = cfg_path.read_text(encoding="utf-8")
data = yaml.safe_load(raw) or {}

def is_catfish_target(name_lower, url_lower):
    return (
        "catfish" in name_lower
        or "catfish" in url_lower
        or "10.10.40.50" in url_lower
        or "localhost:8999" in url_lower
        or "127.0.0.1:8999" in url_lower
    )

def maybe_update(d, label):
    """d is a dict with api_key. Update if it's the catfish-gateway slot."""
    url = (d.get("base_url") or d.get("url") or "").lower()
    name = (d.get("name") or "").lower()
    if not is_catfish_target(name, url):
        return False
    cur = d.get("api_key", "")
    if cur == TEMPLATE:
        print(f"  · {label}: 已是 {TEMPLATE} (skip)")
        return False
    short = (cur[:12] + "...") if cur else "(empty)"
    d["api_key"] = TEMPLATE
    print(f"  ✓ {label}: {short} → {TEMPLATE}")
    return True

changed = False

# top-level model.{base_url, api_key}
model = data.get("model")
if isinstance(model, dict) and "api_key" in model:
    url = (model.get("base_url") or "").lower()
    if is_catfish_target("", url):
        cur = model.get("api_key", "")
        if cur != TEMPLATE:
            short = (cur[:12] + "...") if cur else "(empty)"
            model["api_key"] = TEMPLATE
            print(f"  ✓ model.api_key: {short} → {TEMPLATE}")
            changed = True
        else:
            print(f"  · model.api_key: 已是 {TEMPLATE} (skip)")

# providers.<name>
providers = data.get("providers")
if isinstance(providers, dict):
    for pname, p in providers.items():
        if isinstance(p, dict) and "api_key" in p:
            if maybe_update({**p, "name": pname, "base_url": p.get("base_url") or p.get("url")}, f"providers.{pname}.api_key"):
                p["api_key"] = TEMPLATE
                changed = True

# custom_providers: list of dicts
custom = data.get("custom_providers")
if isinstance(custom, list):
    for i, p in enumerate(custom):
        if isinstance(p, dict) and "api_key" in p:
            label = f"custom_providers[{i}] ({p.get('name','?')}).api_key"
            if maybe_update(p, label):
                changed = True

if changed:
    cfg_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"  ✓ 写回 {cfg_path}")
else:
    print(f"  · {cfg_path} 无需改动 (所有 catfish api_key 已是模板)")
PY

echo
echo "✓ hermes 配置已更新 (.env + config.yaml). 重启 hermes 让新 token 生效:"
echo "    hermes gateway restart"
echo
echo "提示: BL-HERMES-LAUNCHD-ENV-LOAD 之后, launchd 启动 hermes 时会自动 source"
echo "       ~/.hermes/.env (经 ~/.hermes/launchd-wrapper.sh 包装), 不再需要 "
echo "       launchctl setenv HERMES_SERVICE_TOKEN — 重启 hermes 即可."
echo
echo "       一次性安装 wrapper (新机器或回退后): bash ~/.hermes/install-launchd-wrapper.sh"
echo
echo "到期前重跑这个脚本续 token (只动 .env, config.yaml 模板不会 churn). 也可加 cron 每 25 天自动续:"
echo "    0 3 1 * * CLIENT_SECRET=xxx $(realpath "$0") >> ~/.hermes/mint.log 2>&1"
