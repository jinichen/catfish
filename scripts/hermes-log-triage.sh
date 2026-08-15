#!/usr/bin/env bash
# hermes-log-triage.sh — 看 ~/.hermes/logs/agent.log, 但**先说清楚有没有数据**。
#
# ─────────────────────────────────────────────────────────────────────────
# 为什么有这个脚本 (8/15 晚)
#
# 那天晚上连续三次拿"某个计数是 0"当好消息, 三次都是错的:
#
#   1. 账本里「(无标记)」从 40% 掉到 1.8% —— 以为是打标修复生效了。
#      实际是**分母变了**: 那 4 分钟聊天烧了 108 万 token, 把 2 万的插件调用
#      比下去了。插件那会儿还跑着 8/8 的老代码, 修复根本没加载。
#
#   2. 重启后 `grep -c "No module named 'plugin'"` = 0 —— 以为修好了。
#      实际是**重启后一次对话都没跑过**, 那段日志总共 2 行。
#
#   3. 重启后 `grep "wiki Step 2 generation"` 什么都没有 —— 同上。
#
# 三次都是同一个形状: **样本数为 0 时, "没有坏消息"被当成了"好消息"**。
#
# 所以这个脚本的第一职责不是"报告问题", 是**先报告这段时间里有没有发生过事**。
# 没发生过就明说"别下结论", 不给任何绿色。
# ─────────────────────────────────────────────────────────────────────────
#
# 用法:
#   bash scripts/hermes-log-triage.sh              # 看最后一次网关启动之后
#   bash scripts/hermes-log-triage.sh --all        # 看整个日志
#   bash scripts/hermes-log-triage.sh --since "2026-08-15 17:00"
#
set -uo pipefail

LOG="${HERMES_AGENT_LOG:-$HOME/.hermes/logs/agent.log}"
MODE="restart"
SINCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --all) MODE="all"; shift ;;
    --since) MODE="since"; SINCE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "不认识的参数: $1 (试 --help)"; exit 2 ;;
  esac
done

[ -f "$LOG" ] || { echo "❌ 找不到日志: $LOG"; exit 1; }

# 按**显示宽度**补空格 (中文算 2 列)。printf 的 %-Ns 数的是字节, 中英混排会参差。
_pad() {
  local s="$1" want="$2"
  local w; w=$(printf '%s' "$s" | LC_ALL=C.UTF-8 awk '{
    n=0; for(i=1;i<=length($0);i++){c=substr($0,i,1); n += (c ~ /[\x00-\x7F]/) ? 1 : 2} print n
  }' 2>/dev/null || printf '%s' "${#s}")
  local gap=$((want - w)); [ "$gap" -lt 1 ] && gap=1
  printf '%s%*s' "$s" "$gap" ""
}

B=$'\033[1m'; R=$'\033[31m'; Y=$'\033[33m'; G=$'\033[32m'; D=$'\033[2m'; N=$'\033[0m'

# ── 1. 圈定范围 ─────────────────────────────────────────────
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT

case "$MODE" in
  all)
    cp "$LOG" "$TMP"
    RANGE="整个日志"
    ;;
  since)
    awk -v s="$SINCE" '$0 >= s' "$LOG" > "$TMP"
    RANGE="$SINCE 起"
    ;;
  restart)
    # 网关每次起来都会打这句。找最后一次。
    L=$(grep -n "Gateway housekeeping started" "$LOG" | tail -1 | cut -d: -f1)
    if [ -z "$L" ]; then
      echo "${Y}⚠ 日志里找不到网关启动标记, 退回看整个日志${N}"
      cp "$LOG" "$TMP"; RANGE="整个日志 (找不到启动点)"
    else
      tail -n +"$L" "$LOG" > "$TMP"
      RANGE="最后一次网关启动 ($(sed -n "${L}p" "$LOG" | cut -c1-19)) 之后"
    fi
    ;;
esac

echo "${B}══ 范围: $RANGE${N}"
echo "${D}   日志: $LOG   本段 $(wc -l < "$TMP" | tr -d ' ') 行${N}"
echo

# ── 2. ★ 先说有没有数据 ────────────────────────────────────
#
# 这一段是整个脚本存在的理由。下面每一项都是"某个东西跑过几次",
# 跑过 0 次的项, 后面任何关于它的结论都不成立。
echo "${B}══ 这段时间里发生过什么 (样本数)${N}"
declare -a NAMES=(
  "对话轮次|conversation turn"
  "网关请求|POST /v1/chat/completions"
  "记忆总结|catfish-memory sync_turn trigger"
  "wiki 生成|wiki Step 2"
  "早安 advisor|companion-advisor"
)
ANY=0
for item in "${NAMES[@]}"; do
  label="${item%%|*}"; pat="${item##*|}"
  n=$(grep -cF "$pat" "$TMP")
  [ "$n" -gt 0 ] && ANY=1
  # printf 的 %-Ns 数字节, 中文一个字 3 字节 —— 直接用会参差。自己补空格。
  pad=$(_pad "$label" 16)
  if [ "$n" -eq 0 ]; then
    printf "   %s${D}%4d 次   ← 没跑过, 关于它的任何结论都不成立${N}\n" "$pad" "$n"
  else
    printf "   %s${G}%4d 次${N}\n" "$pad" "$n"
  fi
done
echo

if [ "$ANY" -eq 0 ]; then
  echo "${R}${B}╔══════════════════════════════════════════════════════════╗${N}"
  echo "${R}${B}║  这段时间里什么都没发生 —— 下面的"没有错误"不是好消息。  ║${N}"
  echo "${R}${B}╚══════════════════════════════════════════════════════════╝${N}"
  echo
  echo "   刚重启完的话: 去 Companion 里聊一句, 等十几秒后台跑完, 再跑一次本脚本。"
  echo "   验记忆/wiki 的话: 那两条要攒够轮次才触发 (sync_turn 默认每 5 轮)。"
  echo
fi

# ── 3. 警告和错误, 按内容去重 ──────────────────────────────
#
# 去重是关键: 一条反复出现 17 次的 warning 散在 2.8M 日志里根本看不出来,
# 8/15 那个 "wiki 生成整天零产出" 就是这么冒出来的 (每次只有一行, 混在
# 几千行 INFO 中间)。
echo "${B}══ 警告 / 错误 (按内容去重, 次数降序)${N}"
ERRS=$(grep -E " (WARNING|ERROR) " "$TMP" \
       | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:,]+ //' \
       | sed -E 's/\[(api-|20[0-9]{6}_)[a-z0-9_]+\] //' \
       | sort | uniq -c | sort -rn)
if [ -z "$ERRS" ]; then
  if [ "$ANY" -eq 0 ]; then
    echo "   ${D}(本段没有活动, 所以也没有错误 —— 不代表健康)${N}"
  else
    echo "   ${G}没有 WARNING / ERROR${N}"
  fi
else
  echo "$ERRS" | head -20 | while read -r line; do
    cnt="${line%% *}"
    msg=$(echo "$line" | sed -E 's/^ *[0-9]+ //' | cut -c1-104)
    if echo "$msg" | grep -q "ERROR"; then col="$R"; else col="$Y"; fi
    printf "   ${col}%4s${N}  %s\n" "$cnt" "$msg"
  done
  total=$(echo "$ERRS" | wc -l | tr -d ' ')
  [ "$total" -gt 20 ] && echo "   ${D}… 还有 $((total-20)) 种${N}"
fi
echo

# ── 4. 几个已知的"静默失败" ────────────────────────────────
#
# 这些的共同点: 被 except 吞成 warning 或 INFO, 功能不工作但没人会发现。
# 都是 8/15 真踩过的, 列在这儿是为了下次一眼能看到。
echo "${B}══ 已知的静默失败 (踩过的坑, 定点查)${N}"
check() {
  local label="$1" pat="$2" pre="$3" hint="$4"
  local pre_n; pre_n=$(grep -cF "$pre" "$TMP")
  local n; n=$(grep -cF "$pat" "$TMP")
  local pad; pad=$(_pad "$label" 22)
  if [ "$pre_n" -eq 0 ]; then
    printf "   ${D}%s前置「%s」没跑过, 查不了${N}\n" "$pad" "$pre"
  elif [ "$n" -gt 0 ]; then
    printf "   ${R}%s%d 次${N}  %s\n" "$pad" "$n" "$hint"
  else
    printf "   ${G}%s干净${N} ${D}(前置跑过 %d 次)${N}\n" "$pad" "$pre_n"
  fi
}
check "插件兄弟模块 import"  "No module named 'plugin'"      "conversation turn"  "→ _sib 又退回裸 import 了"
check "wiki 生成"            "wiki Step 2 generation 返空"   "wiki Step 2"        "→ prompt 拼不出来? 查 KeyError"
check "prompt KeyError"      "KeyError"                       "wiki Step 2"        "→ 模板里有花括号撞 .format"
check "hermes relay 收尾"    "turn finalization failed"       "conversation turn"  ""
echo

# ── 5. token 大户 ───────────────────────────────────────────
echo "${B}══ 单轮输入 token 最大的几次${N}"
if grep -q "API call #" "$TMP"; then
  # ⚠ 格式是 `API call #1: model=... in=31415` —— **#1 后面有冒号**。
  #   第一版正则写成 `#[0-9]* model=` 少了那个冒号, 整段静默输出空。
  #   这脚本本身就是为了防"0 被当成好消息"的, 结果自己先犯了一次。
  grep -oE "API call #[0-9]+: model=[^ ]+ .*in=[0-9]+" "$TMP" \
    | sed -E 's/.*model=([^ ]+).*in=([0-9]+)/\2 \1/' \
    | sort -rn | head -5 \
    | while read -r tok model; do printf "   %8s  %s\n" "$tok" "$model"; done
  echo "   ${D}(一句"测试"也要 4 万输入的话, 看 catfish-memory prefetch 注了多少)${N}"
else
  echo "   ${D}本段没有 API call 记录${N}"
fi
