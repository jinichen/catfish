#!/usr/bin/env bash
# 五一 sprint 端到端集成测试 — 跑所有 5 天的单元测 + 集成.
#
# 因为只有 gateway 有自己的 venv (其他项目装在 hermes venv 里),
# 所有 pytest 都用 gateway venv (依赖足够) + 各自 src 路径加到 PYTHONPATH.

set -euo pipefail

ROOT="${HOME}/person_task/catfish"
GATEWAY_VENV="${ROOT}/central/llm-gateway/venv"
HERMES_VENV="${HOME}/.hermes/hermes-agent/venv"

PASS_COUNT=0
FAIL_COUNT=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 选 Python: gateway venv 优先, fallback hermes venv, 都没就 system python3
choose_python() {
  if [[ -f "${GATEWAY_VENV}/bin/python" ]]; then
    echo "${GATEWAY_VENV}/bin/python"
  elif [[ -f "${HERMES_VENV}/bin/python" ]]; then
    echo "${HERMES_VENV}/bin/python"
  else
    echo "$(which python3)"
  fi
}

PYTHON=$(choose_python)
echo "使用 Python: ${PYTHON}"

run_pytest_files() {
  local label="$1"
  local proj_src="$2"          # 加到 PYTHONPATH (跨包 import)
  local cwd="$3"
  shift 3
  local files=("$@")

  echo ""
  echo -e "${YELLOW}━━━ ${label} ━━━${NC}"

  if [[ ! -d "${cwd}" ]]; then
    echo -e "  ${RED}✗${NC} 目录不存在: ${cwd}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return
  fi

  cd "${cwd}"
  local extra_path=""
  if [[ -n "${proj_src}" ]] && [[ -d "${proj_src}" ]]; then
    extra_path="${proj_src}:"
  fi

  if PYTHONPATH="${extra_path}${PYTHONPATH:-}" "${PYTHON}" -m pytest -q "${files[@]}"; then
    echo -e "  ${GREEN}✓${NC} ${label} 通过"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✗${NC} ${label} 失败"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# ── 1. gateway 单元测 (新增 4 个测试文件) ─────────────────────

run_pytest_files \
  "Gateway · a2a_allow + a2a_jwt + a2a_audit + skills_loader_version" \
  "${ROOT}/central/llm-gateway/src" \
  "${ROOT}/central/llm-gateway" \
  tests/test_a2a_allow.py \
  tests/test_a2a_audit.py \
  tests/test_a2a_jwt.py \
  tests/test_skills_loader_version.py

# ── 2. identity-server 单元测 ────────────────────────────────

run_pytest_files \
  "Identity-server · registry" \
  "${ROOT}/central/identity-server/src" \
  "${ROOT}/central/identity-server" \
  tests/test_registry.py

# ── 3. tool-bridge 单元测 ────────────────────────────────────

run_pytest_files \
  "Tool-bridge · skill_lifecycle" \
  "${ROOT}/edge/tool-bridge/src" \
  "${ROOT}/edge/tool-bridge" \
  tests/test_skill_lifecycle.py

# ── 4. companion-app parse_file_test ─────────────────────────

run_pytest_files \
  "Companion-app · parse_file" \
  "" \
  "${ROOT}/edge/companion-app/src-tauri" \
  tests/parse_file_test.py

# ── 5. Plan D e2e 集成测试 ──────────────────────────────────

echo ""
echo -e "${YELLOW}━━━ Plan D e2e 集成测试 ━━━${NC}"

E2E_SCRIPT="${ROOT}/scripts/plan_d_e2e_test.sh"
if [[ -f "${E2E_SCRIPT}" ]]; then
  if bash "${E2E_SCRIPT}"; then
    echo -e "  ${GREEN}✓${NC} Plan D e2e 通过"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✗${NC} Plan D e2e 失败 (看 /tmp/catfish-plan-d-e2e/*.log)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
else
  echo -e "  ${RED}✗${NC} ${E2E_SCRIPT} 不存在"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ── 总结 ──

echo ""
echo "════════════════════════════════════════════"
echo -e "通过: ${GREEN}${PASS_COUNT}${NC}  失败: ${RED}${FAIL_COUNT}${NC}"
if [[ ${FAIL_COUNT} -eq 0 ]]; then
  echo -e "${GREEN}✅ 五一 sprint 全部测试通过${NC}"
  exit 0
else
  echo -e "${RED}❌ 有 ${FAIL_COUNT} 项失败${NC}"
  exit 1
fi
