#!/usr/bin/env bash
# Plan D 单机 mock 初始化 — 五一 sprint Day 5.
#
# 在一台 macbook 上模拟 Alice + Bob 2 个员工的 catfish 实例:
#   - 各自独立 home: ~/.catfish-alice, ~/.catfish-bob
#   - 各自 SSO sub: alice@ffcs.cn, bob@ffcs.cn
#   - 各自端口: alice gateway 8999 / identity 8998, bob gateway 9999 / identity 9998
#   - 各自 RSA key (rsa_keygen 生成 / .well-known/jwks.json 暴露)
#   - 各自 ALLOW.md (alice 限制宽, bob 限制严)
#
# 跑完后:
#   - 终端 1: 启 alice gateway + identity (alice 实例)
#   - 终端 2: 启 bob gateway + identity (bob 实例)
#   - 终端 3: alice catfish_a2a_ask(to_sub=bob, question="项目 X 上周进展") 测试

set -euo pipefail

ALICE_HOME="${HOME}/.catfish-alice"
BOB_HOME="${HOME}/.catfish-bob"

echo "📁 创建 alice / bob home"
mkdir -p "${ALICE_HOME}/identity" "${ALICE_HOME}/whisper-models" \
         "${BOB_HOME}/identity" "${BOB_HOME}/whisper-models"

# === 1. 各自 RSA key (RS256 用) ===
gen_keypair() {
  local home="$1"
  if [[ -f "${home}/identity/private.pem" ]]; then
    echo "  ✓ ${home}/identity/private.pem 已存在, 跳过"
    return
  fi
  echo "  🔑 生成 ${home}/identity/private.pem + public.pem"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out "${home}/identity/private.pem" 2>/dev/null
  openssl rsa -in "${home}/identity/private.pem" -pubout \
    -out "${home}/identity/public.pem" 2>/dev/null
}

echo "🔐 生成 RSA keypair (RS256)"
gen_keypair "${ALICE_HOME}"
gen_keypair "${BOB_HOME}"

# === 2. ALLOW.md 模板 ===

cat > "${ALICE_HOME}/ALLOW.md" <<'EOF'
# Alice 的 ALLOW.md (Plan D Federation)

## 工作公开 (任何同事鲶鱼可问)
- 项目 X 进展
- 项目 Y 进展
- 鲶鱼平台架构
- 周报内容

## 部门内同事 (限研发部)
allow_to: department=研发部
- 我对什么技术感兴趣
- 工作时段 / 工作偏好

## 显式拒绝
deny:
- 薪资
- 个人财务
- 私人日程
- 客户隐私数据
EOF

cat > "${BOB_HOME}/ALLOW.md" <<'EOF'
# Bob 的 ALLOW.md (Plan D Federation)

## 工作公开 (任何同事鲶鱼可问)
- 项目 X 进展
- 周报
- ISO27001 / 27000 资质审核

## 限定 purpose=周报
allow_purpose: 周报
- 上周做了什么
- 本周计划

## 显式拒绝
deny:
- 薪资
- 个人财务
- 私人日程
- 客户隐私数据
- 商务机密
EOF

echo "  ✓ ${ALICE_HOME}/ALLOW.md 已写"
echo "  ✓ ${BOB_HOME}/ALLOW.md 已写"

# === 3. registry env (中央 catfish-identity 共享) ===

REGISTRY_PATH="${HOME}/.catfish-identity-mock/registry.yaml"
mkdir -p "$(dirname "${REGISTRY_PATH}")"

cat > "${REGISTRY_PATH}" <<EOF
# Plan D registry (单机 mock, 5/5 verify)
# 真生产由 catfish-identity /registry/register API 自动维护
agents: {}
EOF

echo "  ✓ ${REGISTRY_PATH} 已初始化"

# === 4. 输出启动指引 ===

cat <<EOF

✅ 单机 mock 初始化完毕.

下一步分 3 个 terminal 启动:

# Terminal 1 (中央 catfish-identity registry, 共享)
export CATFISH_REGISTRY_PATH=${REGISTRY_PATH}
cd ~/person_task/catfish/central/identity-server
./venv/bin/python -m catfish_identity --port 8998

# Terminal 2 (Alice 实例)
export CATFISH_HOME=${ALICE_HOME}
export CATFISH_USER_SUB=alice@ffcs.cn
export CATFISH_GATEWAY_URL=http://127.0.0.1:8999
export CATFISH_REGISTRY_URL=http://127.0.0.1:8998
export CATFISH_REGISTRY_PATH=${REGISTRY_PATH}
export CATFISH_ALLOW_PATH=${ALICE_HOME}/ALLOW.md
export CATFISH_A2A_AUDIT_PATH=${ALICE_HOME}/a2a_audit.jsonl
cd ~/person_task/catfish/central/llm-gateway
./venv/bin/python -m catfish_gateway.app --port 8999

# Terminal 3 (Bob 实例)
export CATFISH_HOME=${BOB_HOME}
export CATFISH_USER_SUB=bob@ffcs.cn
export CATFISH_GATEWAY_URL=http://127.0.0.1:9999
export CATFISH_REGISTRY_URL=http://127.0.0.1:8998
export CATFISH_REGISTRY_PATH=${REGISTRY_PATH}
export CATFISH_ALLOW_PATH=${BOB_HOME}/ALLOW.md
export CATFISH_A2A_AUDIT_PATH=${BOB_HOME}/a2a_audit.jsonl
cd ~/person_task/catfish/central/llm-gateway
./venv/bin/python -m catfish_gateway.app --port 9999

# Terminal 4 (验证)
# 注意: gateway 启动时已自动 self_register (含 public_pem), 不用手动 curl register.
# jwks_uri 由中央 registry 自动算 = http://127.0.0.1:8998/registry/agents/<sub>/jwks.json

# 1. 验证 alice / bob 已自动注册
curl -s http://127.0.0.1:8998/registry/list | python3 -m json.tool
# 期望看到 alice + bob 各一条, online=true

# 2. 验证 per-agent jwks 暴露
curl -s http://127.0.0.1:8998/registry/agents/alice@ffcs.cn/jwks.json | python3 -m json.tool
curl -s http://127.0.0.1:8998/registry/agents/bob@ffcs.cn/jwks.json | python3 -m json.tool
# 期望各返 {"keys": [{kty: RSA, kid: alice@ffcs.cn, ...}]}

# 3. Alice 调 Bob (走 Alice gateway 的 /a2a/internal/ask)
curl -X POST http://127.0.0.1:8999/a2a/internal/ask -H "Content-Type: application/json" -d '{
  "from_sub": "alice@ffcs.cn",
  "to_sub": "bob@ffcs.cn",
  "question": "项目 X 上周进展怎么样?",
  "purpose": "周报"
}'
# 期望: {"ok": true, "answer": "...", "chunks_count": N}

# 4. 看 audit
cat ${ALICE_HOME}/a2a_audit.jsonl
cat ${BOB_HOME}/a2a_audit.jsonl

EOF
