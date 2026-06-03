#!/usr/bin/env bash
# BL-HERMES-0152-COMPAT-AUDIT (2026-06-03):
# 扫 hermes 升级后 catfish-xcatfish-user 11 patch + memory_router + prefetch
# 注入路径的兼容性. 升级前后各跑一次, diff 输出就知道哪里漂了.
#
# 用法: bash audit_hermes_compat.sh
# 输出: 11 个 check, 每个 ✓/✗ + 计数. 任何 ✗ 都要查 hermes 该次升级改了啥.

set -u
HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

echo "========================================"
echo "hermes 兼容性 audit"
echo "HERMES_ROOT=$HERMES_ROOT"
[ -f "$HERMES_ROOT/pyproject.toml" ] && echo "版本: $(grep -E '^version' "$HERMES_ROOT/pyproject.toml" | head -1)"
echo "========================================"
echo ""

fail=0
pass=0

check_file() {
    local f="$1"
    if [ -f "$HERMES_ROOT/$f" ]; then
        echo "  ✓ 文件存在: $f"
        ((pass++))
    else
        echo "  ✗ 文件丢失: $f"
        ((fail++))
    fi
}

check_grep() {
    local f="$1" pat="$2" desc="$3" min="${4:-1}"
    local hits=$(grep -cE "$pat" "$HERMES_ROOT/$f" 2>/dev/null || echo 0)
    if [ "$hits" -ge "$min" ]; then
        echo "  ✓ $desc ($hits 处): $f"
        ((pass++))
    else
        echo "  ✗ $desc ($hits 处, 期望 $min+): $f → 模式 '$pat'"
        ((fail++))
    fi
}

echo "── 1. catfish-xcatfish-user 11 patch target 文件 ──"
check_file "agent/agent_init.py"
check_file "run_agent.py"
check_file "agent/auxiliary_client.py"
check_file "agent/title_generator.py"
check_file "gateway/platforms/api_server.py"
check_file "tools/memory_tool.py"
check_file "agent/conversation_loop.py"
echo ""

echo "── 2. _PATCH_TARGETS func/attr ──"
check_grep "agent/agent_init.py" "def init_agent\b" "agent_init.init_agent"
check_grep "agent/auxiliary_client.py" "_MAIN_RUNTIME_FIELDS" "auxiliary_client._MAIN_RUNTIME_FIELDS"
check_grep "agent/auxiliary_client.py" "def _resolve_auto" "auxiliary_client._resolve_auto"
check_grep "agent/auxiliary_client.py" "def _normalize_main_runtime" "auxiliary_client._normalize_main_runtime"
check_grep "agent/title_generator.py" "def auto_title_session" "title_generator.auto_title_session"
check_grep "run_agent.py" "class AIAgent" "run_agent.AIAgent"
check_grep "gateway/platforms/api_server.py" "class APIServerAdapter" "api_server.APIServerAdapter"
echo ""

echo "── 3. AIAgent method ──"
check_grep "run_agent.py" "def _current_main_runtime" "AIAgent._current_main_runtime"
check_grep "run_agent.py" "def _apply_client_headers_for_base_url" "AIAgent._apply_client_headers_for_base_url"
echo ""

echo "── 4. APIServerAdapter method ──"
check_grep "gateway/platforms/api_server.py" "def _create_agent" "APIServerAdapter._create_agent"
check_grep "gateway/platforms/api_server.py" "_cors_headers_for_origin" "APIServerAdapter._cors_headers_for_origin (P8/P9)"
check_grep "gateway/platforms/api_server.py" "_origin_allowed|_check_origin" "APIServerAdapter origin check (P8)"
echo ""

echo "── 5. memory_router 依赖 (catfish-xcatfish-user/memory_router.py 用) ──"
check_grep "tools/memory_tool.py" "def memory_tool" "tools/memory_tool.py memory_tool function"
echo ""

echo "── 6. prefetch 5 数据源注入路径 (catfish-memory plugin 用) ──"
check_grep "agent/conversation_loop.py" "_memory_manager\.prefetch_all|prefetch_all\(" "conversation_loop prefetch_all 调用"
check_grep "agent/conversation_loop.py" "_ext_prefetch_cache|build_memory_context_block" "_ext_prefetch_cache 注入位置" 2
echo ""

echo "── 7. config.yaml char_limit 读取 (Dashboard cap 显示用) ──"
check_grep "agent/agent_init.py" "user_char_limit|memory_char_limit" "agent_init 读 config.yaml memory.char_limit" 2
echo ""

echo "── 8. MemoryManager / MemoryProvider 接口 (catfish-memory plugin 注册用) ──"
check_grep "agent/memory_manager.py" "class MemoryManager|add_provider|prefetch_all" "MemoryManager 接口" 2
check_grep "plugins/memory/__init__.py" "load_memory_provider|register_memory_provider" "plugins.memory loader" 1
echo ""

echo "── 9. tool registry (LLM 真调 tool name 漂移检查) ──"
# hermes 0.14→0.15.1 改过 tool_call/tool_describe/tool_search 名字
# 0.15.2 看会不会又改
ls "$HERMES_ROOT/tools/" 2>/dev/null | head -20
echo ""

echo "========================================"
echo "结果: pass=$pass  fail=$fail"
if [ "$fail" -eq 0 ]; then
    echo "✓ 11 patch + memory + prefetch 全适配, 升级 OK"
    exit 0
else
    echo "✗ $fail 处漂移 — 升级前需要修 catfish-xcatfish-user/plugin.py 真 patch target"
    echo "  或者回滚 hermes 版本"
    exit 1
fi
