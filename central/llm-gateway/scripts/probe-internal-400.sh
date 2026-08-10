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
  out="$(curl -s -m 90 "$URL" -H "Authorization: Bearer $KEY" \
        -H 'Content-Type: application/json' -d "$payload")"
  printf '  %-30s ' "$name"
  printf '%s' "$out" | python3 -c '
import sys, json
raw = sys.stdin.read()

# 流式回来的是 SSE, 不是单个 JSON —— 第一轮没处理这个, 差点把 stream 的结果
# 读成"不是 JSON"。有 data: 前缀就按 SSE 收。
if raw.lstrip().startswith("data:"):
    n = err = 0
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if body == "[DONE]":
            continue
        try:
            ck = json.loads(body)
        except Exception:
            continue
        if ck.get("error"):
            err += 1
            print("❌ SSE 里带 error:", json.dumps(ck["error"], ensure_ascii=False)[:130])
            break
        n += 1
    if not err:
        print(f"✅ ok   (SSE, {n} 个 chunk)")
    raise SystemExit

try:
    d = json.loads(raw)
except Exception:
    print("✗ 返回既不是 JSON 也不是 SSE:", raw[:130]); raise SystemExit
if "choices" in d:
    ch = d["choices"][0]
    m = ch.get("message", {}) or {}
    c = (m.get("content") or "").replace("\n", " ")[:22]
    u = d.get("usage") or {}
    print(f"✅ ok   content={c!r} tool_calls={len(m.get(\"tool_calls\") or [])} "
          f"finish={ch.get(\"finish_reason\")} "
          f"prompt={u.get(\"prompt_tokens\")} out={u.get(\"completion_tokens\")}")
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

echo
echo "── 第二轮: A-D 全通之后剩下的三个变量 ──"
echo "   (线上失败的是**流式**请求 · 32 个工具 · max_tokens 七万量级)"
echo

# E: stream —— traceback 明确落在 litellm 的 async_streaming, 而第一轮全是非流式。
#    这是 A-D 跟线上最大的一个差别。
run "E C + stream=true" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS,\"max_tokens\":60,\"stream\":true}"

# F: max_tokens 提到线上量级。_compute_max_allowed_output_tokens 压缩后算出来
#    ≈72429 (128000 - 41172*1.3 - 2048)。有的服务端对 prompt+max_tokens 超 ctx
#    直接 400, 60 跟 72429 是两个世界。
run "F C + max_tokens=72429" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS,\"max_tokens\":72429}"

# G: 工具数量。线上 sanitizer 之后是 32 个, 第一轮只有 1 个。
#    这里造 32 个结构相同的假工具 —— 测的是"数量/总 schema 体积"这一维,
#    **不是** hermes 真实工具的 schema 长相。G 通不代表真工具没问题,
#    只代表数量本身不是原因。
TOOLS32="$(python3 -c '
import json
t=[{"type":"function","function":{
     "name":f"probe_tool_{i:02d}",
     "description":"探针工具, 只为凑数量, 不会被调用。" * 3,
     "parameters":{"type":"object",
       "properties":{"a":{"type":"string","description":"参数一"},
                     "b":{"type":"integer","description":"参数二"}},
       "required":["a"]}}} for i in range(32)]
print(json.dumps(t, ensure_ascii=False))')"

run "G C + 32 个工具" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS32,\"max_tokens\":60}"

run "H E+F+G 全叠 (最接近线上)" \
  "{\"model\":\"$MODEL\",\"messages\":$MULTI,\"tools\":$TOOLS32,\"max_tokens\":72429,\"stream\":true}"

cat <<'EOF'

── 怎么读 ──
  E 挂   → 流式路径的锅 (线上 traceback 正是 async_streaming)
  F 挂   → prompt + max_tokens 一起超 ctx 就 400,
           修法 = _compute_max_allowed_output_tokens 那个 max(4096, ...) 下限
           要改成"算不出正数就直接拒绝并告诉员工对话太长", 而不是兜个 4096
           硬发出去 —— 那个"防压成 0/负数"的保护正在把超限伪装成裸 400
  G 挂   → 工具数量/总体积, 继续二分砍工具
  全通   → 变量还在别处: 真实工具 schema 长相 / 消息里的图片 / 某条消息的字段。
           这时别再猜了, 上 dump: 让网关在 400 时把真实发出去的 params 存一份,
           拿真身去比对最快。
EOF
