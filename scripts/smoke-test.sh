#!/usr/bin/env bash
# Phase 1 E2E 自动化 — server side bash smoke test (D1, 6/6 鸿波 marathon).
#
# 验证 P15/P27 链路 server side 不退化 (不验 UI, UI 仍走 manual TEST-PLAN).
# 跑 ~30s, exit code 0 = 全过, 非 0 = 某项失败.
#
# 用法:
#   ./scripts/smoke-test.sh
#
# 前提:
#   - hermes daemon 起 (8642), tool-bridge socket 起 (~/.catfish/tool-bridge.sock),
#     catfish-gateway 起 (8999).
#   - HERMES_API_KEY env 配 (hermes auth, 没配脚本读 ~/.hermes/.env).
#
# CI 不跑这条 (ubuntu runner 装不了完整 hermes). 鸿波本机 / dev runner 跑,
# 或加 nightly cron / pre-commit hook (manual install).
#
# 退码:
#   0  all pass
#   1  hermes/gateway/tool-bridge 没起
#   2  chat completions 普通 prompt fail
#   3  approval flow (P15) fail
#   4  P15.2 endpoint fail
#   5  verify log (P44.7) fail

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() {
  PASS=$((PASS + 1))
  echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
  FAIL=$((FAIL + 1))
  echo -e "  ${RED}✗${NC} $1"
  if [[ -n "${2:-}" ]]; then
    echo -e "     ${YELLOW}↳${NC} $2"
  fi
}

section() {
  echo
  echo -e "${YELLOW}━━━ $1 ━━━${NC}"
}

# ─────────────────────────────────────────────────────────────────────
# Section 1 · 进程 / 端口 健康
# ─────────────────────────────────────────────────────────────────────
section "1. 进程 + 端口"

# hermes daemon (8642)
if lsof -iTCP:8642 -sTCP:LISTEN >/dev/null 2>&1; then
  pass "hermes daemon 8642 listening"
else
  fail "hermes daemon 8642 没起" "运行: hermes gateway start"
  exit 1
fi

# catfish-gateway (8999)
if lsof -iTCP:8999 -sTCP:LISTEN >/dev/null 2>&1; then
  pass "catfish-gateway 8999 listening"
else
  fail "catfish-gateway 8999 没起" "运行: cd central/llm-gateway && python -m catfish_gateway.app"
  exit 1
fi

# tool-bridge socket
TB_SOCK="${HOME}/.catfish/tool-bridge.sock"
if [[ -S "$TB_SOCK" ]]; then
  pass "tool-bridge socket 存在: $TB_SOCK"
else
  fail "tool-bridge socket 不存在" "Companion 起来 tool-bridge 自动 spawn (autostart.rs)"
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────
# Section 2 · tool-bridge RPC (Python 级 dispatch)
# ─────────────────────────────────────────────────────────────────────
section "2. tool-bridge RPC"

TB_RESP=$(echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | nc -U "$TB_SOCK" 2>/dev/null | head -1)
if echo "$TB_RESP" | grep -q '"jsonrpc": "2.0"'; then
  TOOL_COUNT=$(echo "$TB_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('result', [])))" 2>/dev/null || echo "?")
  pass "tools/list returns $TOOL_COUNT tools"
else
  fail "tools/list RPC fail" "raw: $(echo "$TB_RESP" | head -c 200)"
fi

# ─────────────────────────────────────────────────────────────────────
# Section 3 · catfish-gateway /v1/catalog (无 auth 普通 endpoint)
# ─────────────────────────────────────────────────────────────────────
section "3. catfish-gateway /v1/catalog"

CATALOG_STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8999/v1/catalog)
if [[ "$CATALOG_STATUS" == "200" ]]; then
  pass "GET /v1/catalog → 200"
else
  fail "GET /v1/catalog → $CATALOG_STATUS"
fi

# ─────────────────────────────────────────────────────────────────────
# Section 4 · P44.7 verify log (确认 plugin 加载 + verify pass)
# ─────────────────────────────────────────────────────────────────────
section "4. P44.7 plugin verify"

if [[ -f "$HOME/.hermes/logs/agent.log" ]]; then
  if grep -q "P15 _stream_q 闭包" "$HOME/.hermes/logs/agent.log" 2>/dev/null; then
    pass "P44.7 verify: P15 _stream_q 闭包检查存在 log"
  else
    fail "P44.7 verify log 缺" "重启 hermes: hermes gateway stop && hermes gateway start"
  fi

  # 最新一次 install 是否 fail-loud
  LAST_INSTALL=$(grep "catfish-xcatfish-user plugin installed ✓\|hermes refactor 破坏" \
    "$HOME/.hermes/logs/agent.log" 2>/dev/null | tail -1)
  if echo "$LAST_INSTALL" | grep -q "破坏"; then
    fail "plugin install 真挂了 (hermes refactor)" "$LAST_INSTALL"
    exit 5
  elif echo "$LAST_INSTALL" | grep -q "✓"; then
    pass "plugin install 最近一次 ✓"
  else
    fail "找不到 plugin install log"
  fi
else
  fail "agent.log 不存在: $HOME/.hermes/logs/agent.log"
fi

# ─────────────────────────────────────────────────────────────────────
# Section 4b · catfish-memory 还在树外, 且真能被 hermes 解析出来 (8/20)
#
# 病因: 软链原来建在 ~/.hermes/hermes-agent/plugins/memory/ —— 而大版本升级
# 换的正是整棵 hermes-agent/ 树, 链跟着没。失败是**静默**的:
#
#   plugins/memory/__init__.py:201
#     """Returns None if the provider is not found or fails to load."""
#     logger.warning(...); return None
#
# 一条 warning 然后返 None, 记忆停摆但 agent 照常回答。
#
# 8/20 已改装到 ~/.hermes/plugins/ (树外, 跟 catfish-xcatfish-user 同级)。
# 这里守两件事: 位置没被改回去 + provider 真解析得出来。
# 单测 (catfish-memory/tests/test_plugin_survives_hermes_upgrade.py) 守代码和
# 位置; 这一条守**真装好的那台机器**。
# ─────────────────────────────────────────────────────────────────────
section "4b. catfish-memory 树外存活"

if [[ -e "$HOME/.hermes/plugins/catfish-memory" || -L "$HOME/.hermes/plugins/catfish-memory" ]]; then
  pass "catfish-memory 软链在树外 (~/.hermes/plugins/)"
else
  fail "catfish-memory 不在 ~/.hermes/plugins/" \
    "跑 edge/hermes-plugins/install-catfish-memory.sh 重装 (树外)"
fi

if [[ -e "$HOME/.hermes/hermes-agent/plugins/memory/catfish-memory" ]]; then
  fail "catfish-memory 又出现在 hermes 树内了" \
    "下次大版本升级会把它静默抹掉 (且并存时会盖住树外那条). 跑 install-catfish-memory.sh"
else
  pass "hermes 树内无 catfish 残留"
fi

# 真解析一次 —— 位置对不代表 hermes 认得出来
HERMES_VENV_PY="$HOME/.hermes/hermes-agent/venv/bin/python"
if [[ -x "$HERMES_VENV_PY" ]]; then
  MEM_OUT=$(cd "$HOME/.hermes/hermes-agent" && "$HERMES_VENV_PY" -c "
import sys; sys.path.insert(0, '.')
from plugins.memory import find_provider_dir
d = find_provider_dir('catfish-memory')
print('FOUND' if d else 'MISSING', d or '')
" 2>&1 | tail -1)
  if [[ "$MEM_OUT" == FOUND* ]]; then
    pass "hermes 解析得到 catfish-memory (${MEM_OUT#FOUND })"
  else
    fail "hermes 解析不到 catfish-memory — 记忆会静默停摆" "raw: $MEM_OUT"
  fi
else
  echo -e "     ${YELLOW}↳${NC} 找不到 $HERMES_VENV_PY, 跳过解析自检"
fi

# ─────────────────────────────────────────────────────────────────────
# Section 5 · P15 approval SSE event (curl chat completions, 触发
# hermes execute_code guard, 验 SSE 流含 hermes.tool.progress event)
# ─────────────────────────────────────────────────────────────────────
section "5. P15 approval SSE flow"

# 准备 auth header — 从 ~/.hermes/.env 读 HERMES_API_KEY 或用 env
if [[ -z "${HERMES_API_KEY:-}" ]] && [[ -f "$HOME/.hermes/.env" ]]; then
  HERMES_API_KEY=$(grep '^HERMES_API_KEY=\|^API_SERVER_KEY=' "$HOME/.hermes/.env" | head -1 | cut -d= -f2- | tr -d '"' || echo "")
fi
if [[ -z "${HERMES_API_KEY:-}" ]]; then
  fail "HERMES_API_KEY env 没配 (本地 ~/.hermes/.env 缺 HERMES_API_KEY / API_SERVER_KEY)"
  echo -e "     ${YELLOW}↳${NC} skip Section 5 — 仍能跑 Section 1-4 + 6"
  PASS=$PASS  # 不算 fail
else
  # POST /v1/chat/completions 含 execute_code prompt, 流式接收 SSE.
  # 用 curl --max-time 8 限制 (P15 _gateway_approval 阻塞 60s, 8s 足够看到
  # approval event 弹出). macOS 没 `timeout` 命令 (Linux 有), 用 curl 自带
  # --max-time 跨平台.
  SSE_OUT=$(curl -sN --max-time 8 -X POST http://127.0.0.1:8642/v1/chat/completions \
    -H "Authorization: Bearer $HERMES_API_KEY" \
    -H "Content-Type: application/json" \
    -H "X-Catfish-User: smoke-test@catfish.dev" \
    -d '{
      "model": "catfish-public-deepseek-flash",
      "stream": true,
      "messages": [
        {"role": "user", "content": "请用 execute_code 跑: import os; print(os.getcwd())"}
      ]
    }' 2>&1 || true)

  if echo "$SSE_OUT" | grep -q "event: hermes.tool.progress"; then
    pass "SSE 流含 event: hermes.tool.progress"
  else
    fail "SSE 流没收到 hermes.tool.progress event" "raw 前 500 字符: $(echo "$SSE_OUT" | head -c 500)"
  fi

  # 验证 approval_pending status (P15 _approval_notify 推的)
  if echo "$SSE_OUT" | grep -q '"status": "approval_pending"\|"status":"approval_pending"'; then
    pass "SSE 含 status: approval_pending (P15 _approval_notify 推送)"
  else
    fail "SSE 没含 approval_pending — P15 没 fire 或 LLM 没调 execute_code"
  fi

  # ─────────────────────────────────────────────────────────────────
  # Section 6 · P15.2 resolve endpoint (POST /v1/sessions/{sid}/approval)
  # ─────────────────────────────────────────────────────────────────
  section "6. P15.2 resolve endpoint"

  # 提取 approval_session_key 从 SSE 流. set -e + pipefail 下, grep 无匹配
  # 返 1 会让 script exit, 加 || true 兜底.
  #
  # regex 真根因 (6/6 鸿波 audit): json.dumps 默认输出 `"key": "value"` (冒号
  # 后有 1 个空格), 老 grep `"approval_session_key":"..."` 要求无空格 → 永远不
  # 匹配, 走 fake sid path. 用 -E 加可选空格 ` *` 通用化.
  SID=$(echo "$SSE_OUT" | grep -oE '"approval_session_key": *"[^"]*"' 2>/dev/null | head -1 | sed -E 's/"approval_session_key": *"//;s/"$//' || true)
  if [[ -z "$SID" ]]; then
    SID="smoke-test-$(date +%s)"
    echo -e "     ${YELLOW}↳${NC} 没从 SSE 拿到 sid, 用 fake $SID 测 endpoint 返码"
  fi

  RESOLVE_RESP=$(curl -s -X POST "http://127.0.0.1:8642/v1/sessions/$SID/approval" \
    -H "Authorization: Bearer $HERMES_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"choice": "deny"}' 2>&1 || true)

  if echo "$RESOLVE_RESP" | grep -q '"resolved":\|"choice":'; then
    pass "POST /v1/sessions/{sid}/approval 返 JSON 含 resolved/choice"
  else
    fail "POST /v1/sessions/{sid}/approval fail" "raw: $(echo "$RESOLVE_RESP" | head -c 200)"
  fi

  # ─────────────────────────────────────────────────────────────────
  # Section 6b · 这条 endpoint 必须验 token (8/20)
  #
  # 6/6 到 8/20, 它一直是裸的: catfish 的 middleware 短路 return, 上游那行
  # _check_auth 在 handler 里, 永远跑不到. 本机任意进程不带 token POST 一下就能
  # 替员工点"批准" —— 而「execute_code 每次必须人工批准」是红线.
  #
  # 单元测试 (tests/test_p15_2_approval_auth.py) 守的是代码; 这一条守的是
  # **真在跑的那个进程** —— 插件没装上 / 装了旧版 / middleware 没进链, 单测都
  # 看不见, 只有真发一个不带 token 的请求才看得见.
  #
  # 这段跑在 HERMES_API_KEY 非空的分支里, 服务端读的是同一个 ~/.hermes/.env,
  # 所以"服务端没配 key 所以放行"这种假警报不会出现.
  # ─────────────────────────────────────────────────────────────────
  NOAUTH_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "http://127.0.0.1:8642/v1/sessions/$SID/approval" \
    -H "Content-Type: application/json" \
    -d '{"choice": "deny"}' 2>&1 || true)

  if [[ "$NOAUTH_CODE" == "401" ]]; then
    pass "不带 token POST /approval 被拒 (401)"
  else
    fail "审批 endpoint 没验 token — 不带 token 拿到 $NOAUTH_CODE, 期望 401" \
      "本机任意进程都能替员工点批准. 查 plugin_approval._require_api_auth 是否装上."
  fi
fi

# ─────────────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────────────
echo
echo -e "${YELLOW}━━━ 汇总 ━━━${NC}"
TOTAL=$((PASS + FAIL))
if [[ $FAIL -eq 0 ]]; then
  echo -e "  ${GREEN}✓ 全过 ($PASS/$TOTAL)${NC}"
  exit 0
else
  echo -e "  ${RED}✗ $FAIL 失败${NC}, ${GREEN}$PASS 通过${NC} (共 $TOTAL)"
  exit 1
fi
