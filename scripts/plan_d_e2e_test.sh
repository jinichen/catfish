#!/usr/bin/env bash
# Plan D 端到端集成测试 — 五一 sprint Day 5.
#
# 自动跑 alice + bob mock + curl 验证完整链路:
#   1. 起 catfish-identity registry
#   2. 起 alice gateway
#   3. 起 bob gateway
#   4. alice register + bob register
#   5. alice 调 bob ('项目 X 进展') → 期望 ok (ALLOW.md 命中)
#   6. alice 调 bob ('我们公司薪资') → 期望 denied (ALLOW.md deny)
#   7. alice 调 charlie (不存在) → 期望 not registered
#   8. 验 audit jsonl 双方各有 1 行 + 字段完整
#
# 跑前提:
#   - 跑过 plan_d_mock_init.sh (生成 alice/bob home + RSA key + ALLOW.md)
#   - 3 个 venv 跑过 (gateway / identity-server / tool-bridge)
#
# 退出码:
#   0 = 全部测试通过
#   1+ = 哪步失败

set -euo pipefail

ALICE_HOME="${HOME}/.catfish-alice"
BOB_HOME="${HOME}/.catfish-bob"
REGISTRY_PATH="${HOME}/.catfish-identity-mock/registry.yaml"
LOG_DIR="/tmp/catfish-plan-d-e2e"
mkdir -p "${LOG_DIR}"

# ─ 颜色 ─
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'  # No Color

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
info() { echo -e "  ${YELLOW}→${NC} $1"; }

FAIL_COUNT=0
PIDS_TO_KILL=()

# polling 检查 endpoint 是否 healthz, 最多 max_secs 秒.
# 用于 gateway 启动慢 (上游 LLM 探活 30+ 秒) 的等待.
wait_for_healthz() {
  local url="$1"
  local max_secs="${2:-60}"
  local elapsed=0
  while (( elapsed < max_secs )); do
    if curl -s -f -m 2 "${url}" > /dev/null 2>&1; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

cleanup() {
  echo ""
  echo "🧹 清理后台进程..."
  # SIGTERM 第一波
  for pid in "${PIDS_TO_KILL[@]}"; do
    kill -15 "${pid}" 2>/dev/null || true
  done
  sleep 1
  # SIGKILL 第二波 (uvicorn 上游探活 hang 的 SIGTERM 可能不响应)
  for pid in "${PIDS_TO_KILL[@]}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  # 端口最终扫尾 — 任何残留按 PID 杀
  for port in 18998 18999 19999; do
    pids=$(lsof -ti tcp:${port} 2>/dev/null || true)
    for p in ${pids}; do kill -9 "${p}" 2>/dev/null || true; done
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── -1. 杀掉之前 e2e 残留进程 (防 18998/18999/19999 端口冲突) ──

echo "🧹 清理 18998 / 18999 / 19999 端口残留进程"
for port in 18998 18999 19999; do
  pids=$(lsof -ti tcp:${port} 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    echo "  port ${port} 占用: ${pids}, SIGTERM..."
    for p in ${pids}; do kill -15 "${p}" 2>/dev/null || true; done
    sleep 2
    # SIGKILL fallback
    pids2=$(lsof -ti tcp:${port} 2>/dev/null || true)
    if [[ -n "${pids2}" ]]; then
      echo "  port ${port} 还活: ${pids2}, SIGKILL..."
      for p in ${pids2}; do kill -9 "${p}" 2>/dev/null || true; done
      sleep 1
    fi
  fi
done

# 二次确认空了
for port in 18998 18999 19999; do
  if lsof -ti tcp:${port} > /dev/null 2>&1; then
    echo -e "  \033[0;31m✗\033[0m port ${port} 仍被占, 手动 kill: \`lsof -ti tcp:${port} | xargs kill -9\`"
    exit 99
  fi
done
echo "  ✓ 18998 / 18999 / 19999 全部清空"

# ── 0. 前置检查 ──

echo "🔍 前置检查"
if [[ ! -f "${ALICE_HOME}/identity/private.pem" ]]; then
  fail "Alice 私钥不存在, 先跑 plan_d_mock_init.sh"
  exit 2
fi
if [[ ! -f "${BOB_HOME}/identity/private.pem" ]]; then
  fail "Bob 私钥不存在, 先跑 plan_d_mock_init.sh"
  exit 2
fi
if [[ ! -f "${ALICE_HOME}/ALLOW.md" ]]; then
  fail "Alice ALLOW.md 不存在"
  exit 2
fi
pass "Alice / Bob home + key 已就绪"

# ── 1. 起 registry ──

echo ""
echo "📡 启动 catfish-identity registry (port 18998)"
# identity-server 没自己的 venv, 用 gateway venv
IDENTITY_PYTHON="${HOME}/person_task/catfish/central/llm-gateway/venv/bin/python"
if [[ ! -f "${IDENTITY_PYTHON}" ]]; then
  IDENTITY_PYTHON="${HOME}/person_task/catfish/central/identity-server/venv/bin/python"
fi
cd "${HOME}/person_task/catfish/central/identity-server"
env \
  CATFISH_REGISTRY_PATH="${REGISTRY_PATH}" \
  CATFISH_REGISTRY_ISSUER="http://127.0.0.1:18998" \
  CATFISH_IDENTITY_PORT=18998 \
  CATFISH_IDENTITY_HOST=127.0.0.1 \
  PYTHONPATH="${HOME}/person_task/catfish/central/identity-server/src:${PYTHONPATH:-}" \
  "${IDENTITY_PYTHON}" -m catfish_identity \
    > "${LOG_DIR}/registry.log" 2>&1 &
REGISTRY_PID=$!
echo "${REGISTRY_PID}" > "${LOG_DIR}/registry.pid"
PIDS_TO_KILL+=("${REGISTRY_PID}")

# 等 registry 起来 (轻服务, 一般 < 5s)
if ! wait_for_healthz "http://127.0.0.1:18998/healthz" 30; then
  fail "registry 30s 内没起来"
  tail -30 "${LOG_DIR}/registry.log"
  exit 3
fi
pass "registry 在 18998"

# ── 2. 起 alice gateway ──

echo ""
echo "🎀 启动 Alice gateway (port 18999)"
cd "${HOME}/person_task/catfish/central/llm-gateway"
env \
  CATFISH_HOME="${ALICE_HOME}" \
  CATFISH_USER_SUB="alice@ffcs.cn" \
  CATFISH_GATEWAY_URL="http://127.0.0.1:18999" \
  CATFISH_REGISTRY_URL="http://127.0.0.1:18998" \
  CATFISH_ALLOW_PATH="${ALICE_HOME}/ALLOW.md" \
  CATFISH_A2A_AUDIT_PATH="${ALICE_HOME}/a2a_audit.jsonl" \
  PORT=18999 \
  ./venv/bin/python -m catfish_gateway.app \
    > "${LOG_DIR}/alice-gateway.log" 2>&1 &
ALICE_PID=$!
echo "${ALICE_PID}" > "${LOG_DIR}/alice.pid"
PIDS_TO_KILL+=("${ALICE_PID}")

# gateway 启动 30+ 秒 (上游 LLM 可达性自检 6 个 model + lifespan 跑 self_register).
# 用 polling 等到 60s.
if ! wait_for_healthz "http://127.0.0.1:18999/healthz" 90; then
  fail "Alice gateway 90s 内没起来"
  tail -30 "${LOG_DIR}/alice-gateway.log"
  exit 4
fi
pass "Alice gateway 在 18999"

# ── 3. 起 bob gateway ──

echo ""
echo "👨 启动 Bob gateway (port 19999)"
cd "${HOME}/person_task/catfish/central/llm-gateway"
env \
  CATFISH_HOME="${BOB_HOME}" \
  CATFISH_USER_SUB="bob@ffcs.cn" \
  CATFISH_GATEWAY_URL="http://127.0.0.1:19999" \
  CATFISH_REGISTRY_URL="http://127.0.0.1:18998" \
  CATFISH_ALLOW_PATH="${BOB_HOME}/ALLOW.md" \
  CATFISH_A2A_AUDIT_PATH="${BOB_HOME}/a2a_audit.jsonl" \
  PORT=19999 \
  ./venv/bin/python -m catfish_gateway.app \
    > "${LOG_DIR}/bob-gateway.log" 2>&1 &
BOB_PID=$!
echo "${BOB_PID}" > "${LOG_DIR}/bob.pid"
PIDS_TO_KILL+=("${BOB_PID}")

if ! wait_for_healthz "http://127.0.0.1:19999/healthz" 90; then
  fail "Bob gateway 90s 内没起来"
  tail -30 "${LOG_DIR}/bob-gateway.log"
  exit 5
fi
pass "Bob gateway 在 19999"

# ── 4. 验证 self_register (gateway 启动 hook 自动注册) ──

echo ""
echo "📝 验证 alice + bob 已经通过 gateway 启动自动 register"

# 给 gateway 1-2 秒做 self_register
sleep 2

LIST=$(curl -s "http://127.0.0.1:18998/registry/list")
if echo "${LIST}" | grep -q "alice@ffcs.cn"; then
  pass "Alice 自动 registered"
else
  fail "Alice 没自动 register: ${LIST}"
fi
if echo "${LIST}" | grep -q "bob@ffcs.cn"; then
  pass "Bob 自动 registered"
else
  fail "Bob 没自动 register: ${LIST}"
fi

# 验 per-agent jwks
JWKS=$(curl -s "http://127.0.0.1:18998/registry/agents/alice@ffcs.cn/jwks.json")
if echo "${JWKS}" | grep -q '"kty":"RSA"'; then
  pass "Alice jwks endpoint 暴露 RSA 公钥"
else
  fail "Alice jwks 失败: ${JWKS}"
fi

# 验 lookup online
LOOKUP=$(curl -s "http://127.0.0.1:18998/registry/lookup?sub=bob@ffcs.cn")
if echo "${LOOKUP}" | grep -q '"online":true'; then
  pass "lookup bob 在线"
else
  fail "lookup bob 失败: ${LOOKUP}"
fi

# ── 5. alice 调 bob (期望 OK) ──

echo ""
echo "🤝 Test 1: Alice 问 Bob '项目 X 进展如何' (期望 OK, ALLOW.md '项目 X 进展' 命中)"

ASK_OK=$(curl -s -X POST http://127.0.0.1:18999/a2a/internal/ask \
  -H "Content-Type: application/json" \
  -d '{
    "from_sub": "alice@ffcs.cn",
    "to_sub": "bob@ffcs.cn",
    "question": "Bob 你的项目 X 进展如何?",
    "purpose": "周报"
  }')

if echo "${ASK_OK}" | grep -q '"ok":true'; then
  pass "Test 1: ALLOW 命中, 返回 ok"
  ANSWER=$(echo "${ASK_OK}" | python3 -c "import sys, json; print(json.load(sys.stdin).get('answer', '')[:80])")
  info "answer 片段: ${ANSWER}"
else
  fail "Test 1: 应该返 ok, 实际: ${ASK_OK}"
fi

# ── 6. alice 调 bob (期望 denied) ──

echo ""
echo "🚫 Test 2: Alice 问 Bob '我们公司薪资水平' (期望 denied, deny 命中)"

ASK_DENY=$(curl -s -X POST http://127.0.0.1:18999/a2a/internal/ask \
  -H "Content-Type: application/json" \
  -d '{
    "from_sub": "alice@ffcs.cn",
    "to_sub": "bob@ffcs.cn",
    "question": "我们公司薪资水平怎么样",
    "purpose": "咨询"
  }')

if echo "${ASK_DENY}" | grep -q '"ok":false' && echo "${ASK_DENY}" | grep -q "denied"; then
  pass "Test 2: deny 命中, 返回 denied"
else
  fail "Test 2: 应该 denied, 实际: ${ASK_DENY}"
fi

# ── 7. alice 调 charlie (不存在) ──

echo ""
echo "❓ Test 3: Alice 问 charlie (未注册) (期望 connection error)"

ASK_404=$(curl -s -X POST http://127.0.0.1:18999/a2a/internal/ask \
  -H "Content-Type: application/json" \
  -d '{
    "from_sub": "alice@ffcs.cn",
    "to_sub": "charlie@ffcs.cn",
    "question": "项目 X"
  }')

if echo "${ASK_404}" | grep -q '"ok":false'; then
  pass "Test 3: charlie 未注册, 返回 false"
else
  fail "Test 3: 应该 not registered, 实际: ${ASK_404}"
fi

# ── 8. 验 audit ──

echo ""
echo "📋 Test 4: 验证 audit jsonl 双方各有事件"

if [[ -f "${ALICE_HOME}/a2a_audit.jsonl" ]]; then
  ALICE_AUDIT_LINES=$(wc -l < "${ALICE_HOME}/a2a_audit.jsonl")
  if [[ ${ALICE_AUDIT_LINES} -ge 3 ]]; then
    pass "Alice audit jsonl ${ALICE_AUDIT_LINES} 行 (期望 ≥3)"
  else
    fail "Alice audit 行数不够 (${ALICE_AUDIT_LINES} < 3)"
  fi
  # 验有 outbound 方向
  if grep -q '"direction": "outbound"' "${ALICE_HOME}/a2a_audit.jsonl"; then
    pass "Alice audit 有 outbound 事件"
  else
    fail "Alice audit 缺 outbound"
  fi
else
  fail "Alice audit 文件不存在: ${ALICE_HOME}/a2a_audit.jsonl"
fi

if [[ -f "${BOB_HOME}/a2a_audit.jsonl" ]]; then
  BOB_AUDIT_LINES=$(wc -l < "${BOB_HOME}/a2a_audit.jsonl")
  if [[ ${BOB_AUDIT_LINES} -ge 1 ]]; then
    pass "Bob audit jsonl ${BOB_AUDIT_LINES} 行 (期望 ≥1)"
  else
    fail "Bob audit 行数不够"
  fi
  # 验有 inbound 方向
  if grep -q '"direction": "inbound"' "${BOB_HOME}/a2a_audit.jsonl"; then
    pass "Bob audit 有 inbound 事件"
  else
    fail "Bob audit 缺 inbound"
  fi
else
  fail "Bob audit 文件不存在"
fi

# ── 总结 ──

echo ""
echo "════════════════════════════════════════════"
if [[ ${FAIL_COUNT} -eq 0 ]]; then
  echo -e "${GREEN}✅ Plan D e2e 集成测试全部通过${NC}"
  exit 0
else
  echo -e "${RED}❌ ${FAIL_COUNT} 项失败. 详情看 ${LOG_DIR}/*.log${NC}"
  exit 1
fi
