#!/usr/bin/env bash
# 内网 Qwen3-VL 多轮 tool call 返裸 400 —— 二分定位 (8/10).
#
# ## 现象
#
# 同一个模型, 更大的请求成功、更小的失败:
#
#   09:40:22  prompt 57902  无 tools  无 tool 消息   → ok
#   09:40:40  prompt 41172  33 tools  有 tool 消息   → 400 (620ms)
#   09:17:08  prompt 29089  33 tools  tool_msgs=0    → ok
#
# 长度被排除了。上游只回一句什么都没有的
#   error: code = 400 reason =  message =  metadata = map[] cause = <nil>
#
# ## 三个候选, 一次分清
#
#   A 纯对话                          ← 基线, 必须 ok
#   B 对话 + tools 定义               ← 只加工具声明
#   C B + assistant.tool_calls + tool ← 再加多轮工具调用 (线上失败的形状)
#   D C + 关掉 thinking               ← 若 C 挂 D 通, 就是 thinking 的锅
#
# 判据:
#   B 挂  → 工具 schema 本身不被接受
#   C 挂 D 通 → thinking 多轮 tool call 要求回传 reasoning_content
#              (deepseek 5/5 踩过同一个, 见 models.yaml 那段注释)
#   C 挂 D 也挂 → 跟 thinking 无关, 是 tool_calls 消息结构
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
# shellcheck disable=SC1091
source .env 2>/dev/null || true

BASE="${INTERNAL_LLM_BASE_QWEN_VISION:-}"
KEY="${INTERNAL_LLM_KEY:-}"
if [[ -z "$BASE" || -z "$KEY" ]]; then
  echo "❌ .env 里没读到 INTERNAL_LLM_BASE_QWEN_VISION / INTERNAL_LLM_KEY" >&2
  exit 2
fi
URL="${BASE%/}/chat/completions"
MODEL="qwen_v3_6_35b_a3b"

TOOLS='[{"type":"function","function":{"name":"get_weather","description":"查天气",
  "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]'

run() {
  local name="$1" payload="$2"
  local out
  out="$(curl -s -m 60 "$URL" -H "Authorization: Bearer $KEY" \
        -H 'Content-Type: application/json' -d "$payload")"
  printf '  %-34s ' "$name"
  printf '%s' "$out" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("✗ 返回不是 JSON:", sys.stdin.read()[:120]); raise SystemExit
if "choices" in d:
    m = d["choices"][0].get("message", {}) or {}
    c = (m.get("content") or "").replace("\n", " ")[:34]
    tc = len(m.get("tool_calls") or [])
    r = len(m.get("reasoning_content") or "")
    print(f"✅ ok   content={c!r} tool_calls={tc} reasoning={r}字")
else:
    print("❌", json.dumps(d, ensure_ascii=False)[:150])
'
}

echo "── 打 $URL ($MODEL)"
echo

run "A 纯对话" \
  "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"max_tokens\":60}"

run "B +tools 定义" \
  "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"tools\":$TOOLS,\"max_tokens\":60}"

# C: 线上失败的形状 —— assistant 带 tool_calls, 后面跟 tool 回复, 再问一句。
#    注意 assistant 那条**没有** reasoning_content (网关就是不回传的, 见 app.py)。
MULTI='[{"role":"user","content":"北京天气怎么样"},
 {"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function",
   "function":{"name":"get_weather","arguments":"{\"city\":\"北京\"}"}}]},
 {"role":"tool","tool_call_id":"c1","content":"晴 26 度"},
 {"role":"user","content":"那上海呢"}]'

run "C +多轮 tool_calls (线上形状)" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS,\"max_tokens\":60}"

run "D C + 关 thinking" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS,\"max_tokens\":60,
    \"chat_template_kwargs\":{\"enable_thinking\":false}}"

cat <<'EOF'

── 怎么读 ──
  B 就挂            → 工具 schema 不被接受, 跟 thinking 无关
  C 挂 · D 通       → thinking + 多轮 tool call 的锅 (deepseek 5/5 同款),
                      修法 = 给这个模型也配 param_overrides 关 thinking
  C 挂 · D 也挂     → 是 tool_calls 消息结构本身, 得继续二分
                      (先去掉 tool 那条只留 assistant.tool_calls 试)
  全通              → 最小复现不够狠, 得往上加 (工具数量 / content 为空 / 图片)
EOF
