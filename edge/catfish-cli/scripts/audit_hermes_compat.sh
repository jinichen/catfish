#!/usr/bin/env bash
# BL-HERMES-0152-COMPAT-AUDIT (2026-06-03):
# 扫 hermes 升级后 catfish-xcatfish-user 19+ patch + memory_router + prefetch
# 注入路径的兼容性. 升级前后各跑一次, diff 输出就知道哪里漂了.
#
# P3.5.47 (2026-06-21 鸿波 hermes v0.17 升级 audit, "不要瞎猜"): 加 section
# 10-16 cover hermes 0.15.2 之后新加的 patch 跟 v0.17 god-file 重构后的依赖
# (P11 connect / P14 _handle_message+session_key_for_source / P15 _run_agent
# +approval 6 fn / P15.2 / P16 session_search_tool / P19 status_callback kwarg).
# v0.17 实测 verified pass 全过 (gateway/run.py 17555 lines, api_server.py
# 4406 lines, 全 19 patch target 跟 6 个 approval fn 都仍存原 path).
#
# 用法: bash audit_hermes_compat.sh
# 输出: 16 个 section, 每个 ✓/✗ + 计数. 任何 ✗ 都要查 hermes 该次升级改了啥.

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
    # P3.5.47 bugfix: 老写 `|| echo 0` 在 grep no-match (return 1) 时拼"0\n0",
    # `[ "0\n0" -ge 1 ]` → "integer expression expected". grep -c 总自己输出
    # 数字 (0 或更大) 到 stdout, 这层 fallback 只为 file 不存在 grep stderr.
    local hits=$(grep -cE "$pat" "$HERMES_ROOT/$f" 2>/dev/null)
    hits=${hits:-0}
    if [ "$hits" -ge "$min" ]; then
        echo "  ✓ $desc ($hits 处): $f"
        ((pass++))
    else
        echo "  ✗ $desc ($hits 处, 期望 $min+): $f → 模式 '$pat'"
        ((fail++))
    fi
}

echo "── 1. catfish-xcatfish-user 19 patch target 文件 ──"
check_file "agent/agent_init.py"
check_file "run_agent.py"
check_file "agent/auxiliary_client.py"
check_file "agent/title_generator.py"
check_file "gateway/platforms/api_server.py"
check_file "tools/memory_tool.py"
check_file "agent/conversation_loop.py"
# P3.5.47 加: P11/P14/P15/P15.2/P16 依赖
check_file "gateway/run.py"
check_file "tools/approval.py"
check_file "tools/session_search_tool.py"
check_file "hermes_cli/tools_config.py"
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
# P3.5.47 update (hermes v0.17 重构): conversation_loop 不再直接调
# _memory_manager.prefetch_all() — 改成走 _ctx.ext_prefetch_cache lazy
# pattern. prefetch_all method 本体仍在 MemoryManager (memory_manager.py
# 真 def), check 移过去. catfish-xcatfish-user 当前不用 prefetch_all
# (BACKLOG: catfish-memory plugin 待办), 这条 check 给将来 plugin 兜.
check_grep "agent/memory_manager.py" "def prefetch_all\b" "MemoryManager.prefetch_all method (catfish-memory plugin 待办)"
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

# ═══════════════════════════════════════════════════════════════════════
# P3.5.47 新加 section 10-16 — cover hermes 0.15.2 之后加的 catfish patch
# 跟 v0.17 god-file 重构后的依赖. 跑这些 check 之前先 verify plugin.py 当前
# 真用了它们 (grep "_run_agent" / "_handle_message" / "register_gateway_notify"
# 等 in edge/hermes-plugins/catfish-xcatfish-user/plugin.py).
# ═══════════════════════════════════════════════════════════════════════

echo "── 10. P15 _run_agent + chat_completions 闭包反射依赖 ──"
# catfish plugin.py:1614 patched_run_agent wrap APIServerAdapter._run_agent,
# 反射 stream_delta_callback.__closure__ 拿 _stream_q. v0.17 _run_agent 仍
# 存 (api_server.py:3571), _handle_chat_completions 内仍创 _stream_q +
# _on_delta 闭包. 任一漂移都 break P15 approval flow.
check_grep "gateway/platforms/api_server.py" "async def _run_agent\b" "APIServerAdapter._run_agent method"
check_grep "gateway/platforms/api_server.py" "async def _handle_chat_completions\b" "APIServerAdapter._handle_chat_completions"
check_grep "gateway/platforms/api_server.py" "_stream_q\s*[:=]" "_stream_q 闭包 freevar (P15 反射) " 1
check_grep "gateway/platforms/api_server.py" "def _on_delta\b" "_on_delta 闭包 (P15 反射)"
check_grep "gateway/platforms/api_server.py" "stream_delta_callback" "stream_delta_callback kwarg (P15 cb 入口)" 2
echo ""

echo "── 11. P11 connect (catfish-xcatfish-user middleware 注入入口) ──"
check_grep "gateway/platforms/api_server.py" "async def connect\b" "APIServerAdapter.connect (P11 wrap 入口)"
echo ""

echo "── 12. P14 GatewayRunner._handle_message + _session_key_for_source ──"
# catfish plugin.py:1551 wrap _handle_message 做中文 approve alias.
# v0.17 god-file 重构 (gateway/run.py 19157→17555 lines, mixin 化), method
# 本体可能漂 — 但 MRO 仍能解析就 OK. Check 本 file 里仍 def 或某 mixin def.
check_grep "gateway/run.py" "async def _handle_message\b" "GatewayRunner._handle_message (P14 wrap)" 1
check_grep "gateway/run.py" "def _session_key_for_source\b" "GatewayRunner._session_key_for_source (P14 调)"
# 4 个 _create_agent 内部用的 module-level fn (hermes 自己用, catfish 不直接调
# 但 catfish P6 patched_create_agent → _orig → import 这 4 个, 漂了 _orig 调爆)
check_grep "gateway/run.py" "^def _current_max_iterations\b" "gateway.run._current_max_iterations"
check_grep "gateway/run.py" "^def _resolve_runtime_agent_kwargs\b" "gateway.run._resolve_runtime_agent_kwargs"
check_grep "gateway/run.py" "^def _resolve_gateway_model\b" "gateway.run._resolve_gateway_model"
check_grep "gateway/run.py" "^def _load_gateway_config\b" "gateway.run._load_gateway_config"
echo ""

echo "── 13. P14/P15/P15.2 approval module 6 fn ──"
# catfish plugin.py:1546/1618-1623/1742 import. 漂任一 → P14 chinese alias /
# P15 approval SSE 桥 / P15.2 resolve 全 break.
check_grep "tools/approval.py" "^def has_blocking_approval\b" "approval.has_blocking_approval (P14)"
check_grep "tools/approval.py" "^def register_gateway_notify\b" "approval.register_gateway_notify (P15)"
check_grep "tools/approval.py" "^def unregister_gateway_notify\b" "approval.unregister_gateway_notify (P15)"
check_grep "tools/approval.py" "^def set_current_session_key\b" "approval.set_current_session_key (P15)"
check_grep "tools/approval.py" "^def reset_current_session_key\b" "approval.reset_current_session_key (P15)"
check_grep "tools/approval.py" "^def resolve_gateway_approval\b" "approval.resolve_gateway_approval (P15.2)"
# _gateway_notify_cbs dict + _approval_session_key contextvar 是 P15 桥的真
# 内部数据结构, 漂了 register/notify 不工作.
check_grep "tools/approval.py" "_gateway_notify_cbs\s*[:=]" "_gateway_notify_cbs dict (P15 桥)" 1
check_grep "tools/approval.py" "_approval_session_key\s*[:=]" "_approval_session_key contextvar (P15)" 1
echo ""

echo "── 14. P16 session_search_tool 存在 (catfish 主入口加速) ──"
# catfish plugin.py:413 import. 仅 file 存在就够 (catfish P16 monkey-patch
# 内部 fn / cache 行为, 漂 fn 名会 silent fall-back 到原慢路径, 不 crash).
check_file "tools/session_search_tool.py"
echo ""

echo "── 15. P19 AIAgent status_callback / stream_delta_callback kwarg ──"
# catfish P19 (plugin.py:2108) 跟 P15 闭包反射都依赖 AIAgent.__init__ 接受
# 这 2 个 callback kwarg. v0.17 仍接受 (run_agent.py:320 AIAgent.__init__
# signature 含两者).
check_grep "run_agent.py" "stream_delta_callback\s*[:=]" "AIAgent.__init__ stream_delta_callback kwarg" 1
check_grep "run_agent.py" "status_callback\s*[:=]" "AIAgent.__init__ status_callback kwarg (P19)" 1
echo ""

echo "── 16. P10 AIAgent._replace_primary_openai_client (catfish P6 调) ──"
# catfish plugin.py:961-962 用 hasattr() 软 check 再调, 漂了 silent skip
# (P6 X-Catfish-User 注入降级到老路径 _apply_client_headers_for_base_url
# 单跑). Check 出来好提前发现 wechat openid header 不重 apply.
check_grep "run_agent.py" "def _replace_primary_openai_client\b" "AIAgent._replace_primary_openai_client (P6 重建 client)"
echo ""

echo "========================================"
echo "结果: pass=$pass  fail=$fail"
if [ "$fail" -eq 0 ]; then
    echo "✓ 19 patch + memory + prefetch + approval/run_agent/connect 全适配, 升级 OK"
    exit 0
else
    echo "✗ $fail 处漂移 — 升级前需要修 catfish-xcatfish-user/plugin.py 真 patch target"
    echo "  或者回滚 hermes 版本"
    exit 1
fi
