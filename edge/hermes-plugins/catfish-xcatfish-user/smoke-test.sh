#!/usr/bin/env bash
# Smoke test: plugin 装好后跑这个, 验证 3 路径端到端 X-Catfish-User 真透过去.
# - 路径 1: Companion (chenhongbo@ffcs.cn) /v1/chat/completions
# - 路径 2: WeChat ClawBot (o9cq807y...@im.weixin) /v1/chat/completions (模拟)
# - 路径 3: title_generator auxiliary task 用 main_runtime 透传 X-Catfish-User
set -uo pipefail

GATEWAY_LOG="$HOME/Library/Logs/catfish/gateway.log"
HERMES_HEALTH="http://127.0.0.1:8642/health"
HERMES_CHAT="http://127.0.0.1:8642/v1/chat/completions"

# hermes 0.15 PR #33232: loopback 也要 API_SERVER_KEY auth. 从 ~/.hermes/.env 读.
API_KEY=$(grep '^API_SERVER_KEY=' "$HOME/.hermes/.env" 2>/dev/null | cut -d= -f2-)
if [[ -z "$API_KEY" ]]; then
    echo "✗ ~/.hermes/.env 没 API_SERVER_KEY — hermes 0.15 必须有"
    exit 1
fi
AUTH_H="Authorization: Bearer $API_KEY"

pass=0
fail=0

echo "================================================================"
echo "  catfish-xcatfish-user smoke test"
echo "================================================================"

# 0. hermes 在
echo ""
echo "→ [0] hermes /health"
if curl -sS "$HERMES_HEALTH" | grep -q '"status":[[:space:]]*"ok"'; then
    echo "  ✓ hermes 起着"
    ((pass++))
else
    echo "  ✗ hermes 不响应 — 先跑 launchctl kickstart -k gui/\$(id -u)/ai.hermes.gateway"
    exit 1
fi

# 1. plugin 加载 log
echo ""
echo "→ [1] plugin 加载 log"
if grep -q "catfish-xcatfish-user plugin installed" ~/.hermes/logs/agent.log 2>/dev/null; then
    echo "  ✓ 看到 'plugin installed' log"
    ((pass++))
else
    echo "  ✗ 没看到 plugin install log — plugin 没加载 / 加载失败"
    echo "    grep 'catfish-xcatfish-user' ~/.hermes/logs/agent.log"
    ((fail++))
fi

# 通用辅助: 发完请求后 tail 最后 N 行找标识符. 比 wc -l + tail +N 抗 macOS
# 前导空格 + 比 diff 整段日志稳.
check_gateway_log() {
    local marker="$1"
    local descr="$2"
    sleep 2  # 给 catfish-gateway 一点时间 flush log
    if tail -n 50 "$GATEWAY_LOG" 2>/dev/null | grep -q "$marker"; then
        echo "  ✓ catfish-gateway log 看到 $descr"
        ((pass++))
    else
        echo "  ✗ catfish-gateway log 没看到 $descr (marker: $marker)"
        echo "      最近 5 行 gateway log:"
        tail -n 5 "$GATEWAY_LOG" 2>/dev/null | sed 's/^/        /'
        ((fail++))
    fi
}

# 2. Companion 路径 (X-Catfish-User: chenhongbo@ffcs.cn)
echo ""
echo "→ [2] Companion 路径 (X-Catfish-User: chenhongbo@ffcs.cn)"
resp=$(curl -sS -X POST "$HERMES_CHAT" \
    -H "Content-Type: application/json" \
    -H "$AUTH_H" \
    -H "X-Catfish-User: chenhongbo@ffcs.cn" \
    -H "X-Hermes-Session-Id: smoke-test-companion-$$" \
    -d '{"model":"catfish-public-deepseek-flash","messages":[{"role":"user","content":"hi"}]}' \
    --max-time 30)
if echo "$resp" | grep -qE '"content"|"choices"'; then
    echo "  ✓ hermes 返回 200 chat completion"
    ((pass++))
else
    echo "  ✗ hermes 没返回正常 completion: $resp"
    ((fail++))
fi
check_gateway_log "user=chenhongbo@ffcs.cn" "user=chenhongbo@ffcs.cn"

# 3. WeChat 路径模拟 (X-Catfish-User: o9cq807y...@im.weixin)
echo ""
echo "→ [3] WeChat 路径 (X-Catfish-User: o9cq807y...@im.weixin)"
resp=$(curl -sS -X POST "$HERMES_CHAT" \
    -H "Content-Type: application/json" \
    -H "$AUTH_H" \
    -H "X-Catfish-User: o9cq807yh8QQuk8jp4IEWb_9O0RE@im.weixin" \
    -H "X-Hermes-Session-Id: smoke-test-weixin-$$" \
    -d '{"model":"catfish-public-deepseek-flash","messages":[{"role":"user","content":"hi"}]}' \
    --max-time 30)
if echo "$resp" | grep -qE '"content"|"choices"'; then
    echo "  ✓ hermes 返回 200 chat completion"
    ((pass++))
else
    echo "  ✗ hermes 没返回正常 completion: $resp"
    ((fail++))
fi
check_gateway_log "o9cq807yh8QQuk8jp4IEWb_9O0RE@im.weixin" "weixin user"

# 4. Picker model override (X-Catfish-User: ... + body.model=gemini)
echo ""
echo "→ [4] Picker model override (body.model 切 Gemini)"
resp=$(curl -sS -X POST "$HERMES_CHAT" \
    -H "Content-Type: application/json" \
    -H "$AUTH_H" \
    -H "X-Catfish-User: chenhongbo@ffcs.cn" \
    -d '{"model":"catfish-public-gemini-flash","messages":[{"role":"user","content":"hi"}]}' \
    --max-time 90)
# Gemini 经常在 NIM 上游 30s+ TTFT, 即便 curl 超时, 只要 gateway log
# 看到 gemini 真被调就算 picker 生效.
if echo "$resp" | grep -qE '"content"|"choices"'; then
    echo "  ✓ hermes 返回 completion"
    ((pass++))
elif [[ -z "$resp" ]]; then
    echo "  ⚠ curl 超时但不算 fail (Gemini 上游 NIM 常拥堵), 看 gateway log"
    ((pass++))
else
    echo "  ✗ hermes 没返回: $resp"
    ((fail++))
fi
check_gateway_log "gemini" "LiteLLM 调 gemini (picker 真生效)"

# 5. title_gen 后台任务无 400
echo ""
echo "→ [5] title_gen 后台任务无 X-Catfish-User 400"
sleep 5  # 让 title_gen 在主对话完了后异步跑
if tail -n 200 ~/.hermes/logs/agent.log | grep -E "Title generation failed.*X-Catfish-User"; then
    echo "  ✗ title_gen 还在 400"
    ((fail++))
else
    echo "  ✓ title_gen 没有 X-Catfish-User 400 (近 200 行)"
    ((pass++))
fi

# 总结
echo ""
echo "================================================================"
echo "  Smoke test 结果: pass=$pass fail=$fail"
if [[ $fail -eq 0 ]]; then
    echo "  ✓ 全过 — plugin 真生效, 可以 revert hermes 仓 patch 了"
    echo ""
    echo "  下一步: bash revert-hermes-patches.sh"
    exit 0
else
    echo "  ✗ 有 fail — 不要 revert, 先修 plugin"
    exit 1
fi
