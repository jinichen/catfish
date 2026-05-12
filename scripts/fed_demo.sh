#!/usr/bin/env bash
# BL-FED2.5 (5/12 鸿波拍板) — 跨员工 demo: 3-agent (alice + bob + charlie) 完整 BL-FED2.1-2.4 闭环.
#
# 演示 5/14 真卖点:
#   1. **专长抽取** (BL-FED2.1): bob/charlie 各有 confirmed expertise.yaml
#   2. **黄页 endpoint** (BL-FED2.2): GET /registry/by-expertise?tag=资质审核
#   3. **跨员工路由** (BL-FED2.3): alice 问 → 黄页查 → 路由到 bob
#   4. **反馈环** (BL-FED2.4): bob 答完 → bob employee_journal.md 多一条 [a2a-help]
#
# 验证步骤:
#   ✓ 起 identity-server (18998) + 3 个 gateway (alice 18999 / bob 19999 / charlie 20999)
#   ✓ 各自 self_register, registry/list 看到 3 个 agent online
#   ✓ by-expertise?tag=资质审核 → 返 bob (脱敏: 不含 jwks/pem/endpoint)
#   ✓ by-expertise?tag=合同审查 → 返 charlie
#   ✓ by-expertise?tag=完全没人懂 → matched_count=0
#   ✓ alice → /a2a/internal/ask → bob: '资质审核怎么搞?' → 返 ok
#   ✓ bob 的 employee_journal.md 多一条 ## [a2a-help] 协助 alice@ffcs.cn
#
# 跑前提:
#   - identity-server / llm-gateway / tool-bridge 各自 venv 已建
#   - alembic upgrade head 已跑 (BL-FED2.2 expertise 列要在)
#
# 退出码: 0 = 全过, 1+ = 失败步骤.

set -euo pipefail

ALICE_HOME="${HOME}/.catfish-alice"
BOB_HOME="${HOME}/.catfish-bob"
CHARLIE_HOME="${HOME}/.catfish-charlie"
REGISTRY_PATH="${HOME}/.catfish-identity-mock/registry.yaml"
LOG_DIR="/tmp/catfish-fed-demo"
mkdir -p "${LOG_DIR}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
info() { echo -e "  ${YELLOW}→${NC} $1"; }

FAIL_COUNT=0
PIDS_TO_KILL=()

wait_for_healthz() {
  local url="$1"
  local max_secs="${2:-90}"
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
  for pid in "${PIDS_TO_KILL[@]}"; do
    kill -15 "${pid}" 2>/dev/null || true
  done
  sleep 1
  for pid in "${PIDS_TO_KILL[@]}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  for port in 18998 18999 19999 20999; do
    pids=$(lsof -ti tcp:${port} 2>/dev/null || true)
    for p in ${pids}; do kill -9 "${p}" 2>/dev/null || true; done
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── -1. 杀残留进程 ──

echo "🧹 清理 18998 / 18999 / 19999 / 20999 端口残留"
for port in 18998 18999 19999 20999; do
  pids=$(lsof -ti tcp:${port} 2>/dev/null || true)
  if [[ -n "${pids}" ]]; then
    for p in ${pids}; do kill -15 "${p}" 2>/dev/null || true; done
    sleep 2
    pids2=$(lsof -ti tcp:${port} 2>/dev/null || true)
    if [[ -n "${pids2}" ]]; then
      for p in ${pids2}; do kill -9 "${p}" 2>/dev/null || true; done
      sleep 1
    fi
  fi
done

# ── 0. 自动初始化 charlie home (复用 plan_d_mock_init.sh 的模式) ──

gen_keypair() {
  local home="$1"
  mkdir -p "${home}/identity"
  if [[ -f "${home}/identity/private.pem" ]]; then
    return
  fi
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out "${home}/identity/private.pem" 2>/dev/null
  openssl rsa -in "${home}/identity/private.pem" -pubout \
    -out "${home}/identity/public.pem" 2>/dev/null
}

ensure_alice() {
  if [[ ! -f "${ALICE_HOME}/identity/private.pem" ]]; then
    fail "Alice home (${ALICE_HOME}) 不存在 — 先跑 scripts/plan_d_mock_init.sh"
    exit 2
  fi
}

ensure_bob() {
  if [[ ! -f "${BOB_HOME}/identity/private.pem" ]]; then
    fail "Bob home (${BOB_HOME}) 不存在 — 先跑 scripts/plan_d_mock_init.sh"
    exit 2
  fi
  # 给 bob 写 expertise.yaml (BL-FED2.1 confirmed tag — 黄页用)
  cat > "${BOB_HOME}/expertise.yaml" <<'YAML'
extracted_at: 2026-05-12T10:00:00
source: employee_journal.md
auto_review_pending: false
tags:
  - tag: 资质审核
    confidence: 0.92
    evidence_count: 27
    aliases: [资质, 资格审查]
    status: confirmed
    last_reviewed_at: 2026-05-12T10:00:00
  - tag: 外勤报销
    confidence: 0.85
    evidence_count: 15
    aliases: [报销, 出差报销]
    status: confirmed
    last_reviewed_at: 2026-05-12T10:00:00
YAML

  # bob ALLOW.md 必须放行 expert_consult:资质审核 这个新 purpose, 否则 a2a 会 denied
  # ★ ALLOW.md 解析规则: 一个 ## section 只能挂一个 allow_purpose: 第二个会覆盖
  #   所以每个 purpose 要起独立的 ## section.
  # ★ keyword 匹配走 jieba token 子集 — "资质审核" 作 keyword tokens={资质, 审核}
  #   能匹配 question "资质审核怎么搞? 客户周三要交材料" (tokens 包含资质+审核).
  # 幂等重写: 先删旧 BL-FED2.3 段 (如果有), 再 append fresh, 避免上次错版本残留.
  python3 - "${BOB_HOME}/ALLOW.md" <<'PYEOF'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    t = f.read()
# 删 BL-FED2.3 开头到下个 ## 或 EOF 之间的所有内容 (含本身)
t = re.sub(r'\n## BL-FED2\.3 .*?(?=\n## |\Z)', '', t, flags=re.S)
with open(p, "w", encoding="utf-8") as f:
    f.write(t)
PYEOF

  cat >> "${BOB_HOME}/ALLOW.md" <<'EOF'

## BL-FED2.3 跨员工路由 — 资质审核
allow_purpose: expert_consult:资质审核
- 资质审核
- 资质材料
- 资质年审

## BL-FED2.3 跨员工路由 — 外勤报销
allow_purpose: expert_consult:外勤报销
- 外勤报销
- 报销标准
- 报销流程
EOF
}

ensure_charlie() {
  echo "🆕 charlie home 自动初始化 (BL-FED2.5 demo 用)"
  mkdir -p "${CHARLIE_HOME}/identity"
  gen_keypair "${CHARLIE_HOME}"
  cat > "${CHARLIE_HOME}/ALLOW.md" <<'EOF'
# Charlie 的 ALLOW.md (BL-FED2.5 demo)

## 工作公开
- 合同审查
- 合规咨询

## BL-FED2.3 跨员工路由 — 合同审查
allow_purpose: expert_consult:合同审查
- 合同审查
- 合同条款
- 合规要点

## 显式拒绝
deny:
- 薪资
- 个人财务
- 客户隐私
EOF
  cat > "${CHARLIE_HOME}/expertise.yaml" <<'YAML'
extracted_at: 2026-05-12T10:00:00
source: employee_journal.md
auto_review_pending: false
tags:
  - tag: 合同审查
    confidence: 0.95
    evidence_count: 32
    aliases: [合同, 合规审查]
    status: confirmed
    last_reviewed_at: 2026-05-12T10:00:00
YAML
  # 清空 employee_journal 让验 BL-FED2.4 写入 fresh
  rm -f "${CHARLIE_HOME}/employee_journal.md"
  pass "Charlie home + expertise + ALLOW 就绪"
}

ensure_alice
ensure_bob
ensure_charlie

# 清空 bob employee_journal 才能干净验 BL-FED2.4 写入
rm -f "${BOB_HOME}/employee_journal.md"

# ── 1. 起 identity-server ──

echo ""
echo "📡 启动 catfish-identity registry (port 18998)"
IDENTITY_PYTHON="${HOME}/person_task/catfish/central/identity-server/venv/bin/python"
[[ -f "${IDENTITY_PYTHON}" ]] || IDENTITY_PYTHON="${HOME}/person_task/catfish/central/llm-gateway/venv/bin/python"

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
PIDS_TO_KILL+=("${REGISTRY_PID}")

if ! wait_for_healthz "http://127.0.0.1:18998/healthz" 30; then
  fail "registry 30s 内没起来"
  tail -30 "${LOG_DIR}/registry.log"
  exit 3
fi
pass "registry 在 18998"

# ── 2. 起 alice / bob / charlie gateway ──

start_gateway() {
  local name="$1"; local home="$2"; local sub="$3"; local port="$4"
  echo ""
  echo "🎀 启动 ${name} gateway (port ${port})"
  cd "${HOME}/person_task/catfish/central/llm-gateway"
  # ★ Demo 故意 unset 上游 LLM key 让 a2a_server.py 走 mock 答案路径 (split_into_chunks
  #   产生明确的 [mock 回答 — 没 DASHSCOPE_API_KEY ...] 字符串). 这样 demo 不依赖
  #   外部 LLM 后端可达, 全链路 (a2a + ALLOW + journal hook) 跑通.
  #   想真 LLM 测试用 scripts/plan_d_e2e_test.sh 或 export CATFISH_FED_DEMO_USE_REAL_LLM=1.
  local llm_overrides="DASHSCOPE_API_KEY="
  if [[ "${CATFISH_FED_DEMO_USE_REAL_LLM:-}" == "1" ]]; then
    llm_overrides=""  # 透传 shell 里的真 key
  fi
  env \
    ${llm_overrides} \
    CATFISH_HOME="${home}" \
    CATFISH_USER_SUB="${sub}" \
    CATFISH_GATEWAY_URL="http://127.0.0.1:${port}" \
    CATFISH_REGISTRY_URL="http://127.0.0.1:18998" \
    CATFISH_ALLOW_PATH="${home}/ALLOW.md" \
    CATFISH_A2A_AUDIT_PATH="${home}/a2a_audit.jsonl" \
    PORT="${port}" \
    ./venv/bin/python -m catfish_gateway.app \
      > "${LOG_DIR}/${name}-gateway.log" 2>&1 &
  local pid=$!
  PIDS_TO_KILL+=("${pid}")
  if ! wait_for_healthz "http://127.0.0.1:${port}/healthz" 90; then
    fail "${name} gateway 90s 内没起来"
    tail -40 "${LOG_DIR}/${name}-gateway.log"
    exit 4
  fi
  pass "${name} gateway 在 ${port}"
}

start_gateway "alice"   "${ALICE_HOME}"   "alice@ffcs.cn"   18999
start_gateway "bob"     "${BOB_HOME}"     "bob@ffcs.cn"     19999
start_gateway "charlie" "${CHARLIE_HOME}" "charlie@ffcs.cn" 20999

# self_register 已在 lifespan 跑过, 给 1-2 秒确认进 registry
sleep 2

# ── 3. 验 self_register + expertise 上报 (BL-FED2.2) ──

echo ""
echo "📝 Test: 3 个 agent 自动 register + bob/charlie expertise 字段已上报"
LIST=$(curl -s "http://127.0.0.1:18998/registry/list")
for sub in alice@ffcs.cn bob@ffcs.cn charlie@ffcs.cn; do
  if echo "${LIST}" | grep -q "${sub}"; then
    pass "${sub} registered"
  else
    fail "${sub} 没 register: ${LIST}"
  fi
done

# 验 bob 的 expertise 字段 (registry/lookup 能看到)
BOB_LOOKUP=$(curl -s "http://127.0.0.1:18998/registry/lookup?sub=bob@ffcs.cn")
if echo "${BOB_LOOKUP}" | grep -q '"资质审核"'; then
  pass "Bob expertise=[资质审核, 外勤报销] 已上报"
else
  fail "Bob expertise 缺: ${BOB_LOOKUP}"
fi

CHARLIE_LOOKUP=$(curl -s "http://127.0.0.1:18998/registry/lookup?sub=charlie@ffcs.cn")
if echo "${CHARLIE_LOOKUP}" | grep -q '"合同审查"'; then
  pass "Charlie expertise=[合同审查] 已上报"
else
  fail "Charlie expertise 缺: ${CHARLIE_LOOKUP}"
fi

# ── 4. 验 by-expertise 黄页 endpoint (BL-FED2.2) ──

echo ""
echo "📋 Test: GET /registry/by-expertise — 黄页查询"

BY_TAG=$(curl -s "http://127.0.0.1:18998/registry/by-expertise?tag=%E8%B5%84%E8%B4%A8%E5%AE%A1%E6%A0%B8")  # 资质审核 URL-encode
if echo "${BY_TAG}" | grep -q '"matched_count":1' && echo "${BY_TAG}" | grep -q "bob@ffcs.cn"; then
  pass "by-expertise?tag=资质审核 → bob (matched_count=1)"
else
  fail "by-expertise?tag=资质审核 错: ${BY_TAG}"
fi

BY_TAG2=$(curl -s "http://127.0.0.1:18998/registry/by-expertise?tag=%E5%90%88%E5%90%8C%E5%AE%A1%E6%9F%A5")  # 合同审查
if echo "${BY_TAG2}" | grep -q "charlie@ffcs.cn"; then
  pass "by-expertise?tag=合同审查 → charlie"
else
  fail "by-expertise?tag=合同审查 错: ${BY_TAG2}"
fi

# 隐私边界: 不含 jwks/pem/endpoint
if echo "${BY_TAG}" | grep -qE 'jwks_uri|public_pem|catfish_endpoint'; then
  fail "🔒 隐私边界破: by-expertise 返了 jwks/pem/endpoint! ${BY_TAG}"
else
  pass "🔒 by-expertise 不返敏感字段 (jwks/pem/endpoint 都不在)"
fi

# 没人懂的 tag → 空
BY_TAG3=$(curl -s "http://127.0.0.1:18998/registry/by-expertise?tag=%E5%AE%8C%E5%85%A8%E6%B2%A1%E4%BA%BA%E6%87%82")
if echo "${BY_TAG3}" | grep -q '"matched_count":0'; then
  pass "by-expertise?tag=完全没人懂 → matched_count=0"
else
  fail "无匹配应返 0, 实际: ${BY_TAG3}"
fi

# ── 5. 跨员工路由: alice → bob (走 a2a) ──

echo ""
echo "🤝 Test: Alice 问 Bob '资质审核怎么搞' (走 a2a, BL-FED2.3 模拟)"
ASK=$(curl -s -X POST http://127.0.0.1:18999/a2a/internal/ask \
  -H "Content-Type: application/json" \
  -d '{
    "from_sub": "alice@ffcs.cn",
    "to_sub": "bob@ffcs.cn",
    "question": "资质审核怎么搞? 客户周三要交材料",
    "purpose": "expert_consult:资质审核"
  }')

if echo "${ASK}" | grep -q '"ok":true'; then
  pass "Alice → Bob 资质审核 a2a OK"
  ANSWER=$(echo "${ASK}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('answer','')[:80])" 2>/dev/null || echo "")
  info "answer 片段: ${ANSWER}"
else
  fail "a2a 失败: ${ASK}"
fi

# 给反馈环 200ms 写 journal
sleep 1

# ── 6. 验 BL-FED2.4 反馈环: bob employee_journal 多了 [a2a-help] ──

echo ""
echo "📓 Test: BL-FED2.4 反馈环 — bob employee_journal 应有 [a2a-help] 条目"
BOB_JOURNAL="${BOB_HOME}/employee_journal.md"
if [[ -f "${BOB_JOURNAL}" ]]; then
  if grep -q "\[a2a-help\]" "${BOB_JOURNAL}"; then
    pass "Bob journal 有 [a2a-help] 标记"
  else
    fail "Bob journal 没 [a2a-help] (反馈环没触发?)"
    info "Bob journal 内容:"
    cat "${BOB_JOURNAL}" | head -20
  fi
  if grep -q "alice@ffcs.cn" "${BOB_JOURNAL}"; then
    pass "Bob journal 记录了 alice@ffcs.cn 是请求方"
  else
    fail "Bob journal 没记录 alice@ffcs.cn"
  fi
  if grep -q "expert_consult:资质审核" "${BOB_JOURNAL}"; then
    pass "Bob journal 记录了 purpose=expert_consult:资质审核"
  else
    fail "Bob journal 没记录 purpose"
  fi
  if grep -q "资质审核怎么搞" "${BOB_JOURNAL}"; then
    pass "Bob journal 记录了原问题摘要"
  else
    fail "Bob journal 没记录问题"
  fi
else
  fail "Bob employee_journal.md 不存在: ${BOB_JOURNAL}"
fi

# ── 7. 验 audit 完整 ──

echo ""
echo "📋 Test: audit jsonl 完整"
if [[ -f "${ALICE_HOME}/a2a_audit.jsonl" ]] && grep -q '"direction": "outbound"' "${ALICE_HOME}/a2a_audit.jsonl"; then
  pass "Alice audit 有 outbound 事件"
else
  fail "Alice audit outbound 缺"
fi
if [[ -f "${BOB_HOME}/a2a_audit.jsonl" ]] && grep -q '"direction": "inbound"' "${BOB_HOME}/a2a_audit.jsonl"; then
  pass "Bob audit 有 inbound 事件"
else
  fail "Bob audit inbound 缺"
fi

# ── 总结 ──

echo ""
echo "════════════════════════════════════════════════════════════"
if [[ ${FAIL_COUNT} -eq 0 ]]; then
  echo -e "${GREEN}✅ BL-FED2.5 三 agent demo 全部通过${NC}"
  echo ""
  echo "📈 Demo 演示步骤 (5/14 给客户演的版本):"
  echo "  1. 'bob 在 mac 上写了 N 周 journal' → catfish_extract_expertise + confirm → expertise.yaml"
  echo "  2. bob gateway 启动 → self_register 把 confirmed expertise 上报到中央 registry"
  echo "  3. alice 问 '资质审核怎么搞?' → catfish_expert_consult 路由到 bob"
  echo "  4. bob 答完 → 反馈环自动写 [a2a-help] 到 bob 自己 journal"
  echo "  5. 下次 bob 跑 catfish_extract_expertise → confidence 自然加权 → 自学习闭环"
  echo ""
  echo "📂 资产:"
  echo "  - registry: ${REGISTRY_PATH}"
  echo "  - bob expertise: ${BOB_HOME}/expertise.yaml"
  echo "  - bob a2a-help journal: ${BOB_HOME}/employee_journal.md"
  echo "  - audit: ${ALICE_HOME}/a2a_audit.jsonl, ${BOB_HOME}/a2a_audit.jsonl"
  exit 0
else
  echo -e "${RED}❌ ${FAIL_COUNT} 项失败. 详情看 ${LOG_DIR}/*.log${NC}"
  exit 1
fi
