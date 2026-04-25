#!/bin/bash
# Smoke tests for Catfish Gateway.
# Run after `python -m catfish_gateway.app` (or docker compose up) is live.

set -e

# --- 找 .env（优先当前目录，否则脚本所在目录的父目录） ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f ".env" ]; then
  ENV_FILE=".env"
elif [ -f "$SCRIPT_DIR/../.env" ]; then
  ENV_FILE="$SCRIPT_DIR/../.env"
else
  ENV_FILE=""
fi

# --- 从 .env 读字段的小工具 ---
read_env() {
  local key="$1"
  [ -z "$ENV_FILE" ] && return
  grep -E "^${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

# --- PORT：shell > .env > 默认 8000 ---
if [ -z "$ENDPOINT" ]; then
  PORT_FROM_ENV=$(read_env PORT)
  PORT="${PORT_FROM_ENV:-8000}"
  ENDPOINT="http://localhost:${PORT}"
fi

# --- TOKEN：shell > .env > 默认 ---
if [ -z "$CATFISH_DEV_TOKEN" ]; then
  CATFISH_DEV_TOKEN=$(read_env CATFISH_DEV_TOKEN)
fi
TOKEN="${CATFISH_DEV_TOKEN:-dev-token-local}"

if [ -n "$ENV_FILE" ]; then
  echo "  (读取配置自 $ENV_FILE)"
fi

have_jq() { command -v jq >/dev/null 2>&1; }
pretty() { if have_jq; then jq .; else cat; fi }

# 统一的 curl 封装：打印状态码，失败时把响应体也打出来
# 用法： req "<name>" <METHOD> <PATH> [curl-data-args...]
req() {
  local name="$1"; shift
  local method="$1"; shift
  local path="$1"; shift

  echo ""
  echo "▶ $name  [$method $path]"

  local tmp code
  tmp=$(mktemp)
  if ! code=$(curl -sS -o "$tmp" -w "%{http_code}" \
      -X "$method" "$ENDPOINT$path" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      "$@"); then
    echo "  ✖ curl 连接失败（服务没起来？）"
    rm -f "$tmp"
    return 1
  fi

  if [ "$code" -ge 200 ] && [ "$code" -lt 300 ]; then
    cat "$tmp" | pretty
  else
    echo "  ✖ HTTP $code"
    cat "$tmp"
    echo ""
  fi
  rm -f "$tmp"
}

token_preview() {
  local t="$1"
  local n=${#t}
  if [ "$n" -le 8 ]; then
    echo "<short:${t}>"
  else
    echo "${t:0:4}...${t: -4} (len=$n)"
  fi
}

echo "════════════════════════════════════════════════════"
echo " 鲶鱼网关冒烟测试 · $ENDPOINT"
echo " 使用 token: $(token_preview "$TOKEN")"
echo "════════════════════════════════════════════════════"

# 1/5 健康检查（不需要 auth）
echo ""
echo "▶ [1/5] 健康检查  [GET /health]"
curl -sS "$ENDPOINT/health" | pretty

# 2/5
req "[2/5] 模型列表（OpenAI 兼容）" GET /v1/models

# 3/5
req "[3/5] 模型目录（Hermes UI 用）" GET /v1/catalog

# 4/5
req "[4/5] 简单对话" POST /v1/chat/completions --data '{
  "model": "catfish-private-main",
  "messages": [{"role": "user", "content": "你好，一句话介绍你自己。"}]
}'

# 5/5
req "[5/5] 工具调用" POST /v1/chat/completions --data '{
  "model": "catfish-private-main",
  "messages": [{"role": "user", "content": "今天北京天气怎么样？"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "查询天气",
      "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      }
    }
  }]
}'

echo ""
echo "════════════════════════════════════════════════════"
echo " 5 步都返回合理 JSON = 链路已打通 ✓"
echo "════════════════════════════════════════════════════"
