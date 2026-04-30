#!/usr/bin/env bash
#
# setup_b_plan.sh — 一键完成 B 方案迁移 (4 步合一)
# ==============================================
# 鸿波 4-30: A 方案 (catfish_run_skill 套件) 过度设计, 切换到 B 方案 (hermes 原生
# catfish-* skill). 这个脚本一次性跑完所有迁移步骤.
#
# 步骤:
#   1. unset proxy (鸿波本机 proxy 经常挂)
#   2. hermes venv 装 python-docx + openpyxl (走清华镜像)
#   3. 跑 install_to_hermes.sh 复制 catfish skill 到 ~/.hermes/skills/productivity/
#   4. 杀 gateway + tool-bridge (Companion watchdog 会拉新的)
#
# 跑法:
#   bash ~/person_task/catfish/scripts/setup_b_plan.sh
#
# 跑完之后:
#   - Companion 仪表盘 productivity 段应该多出 catfish-leadership-briefing + catfish-weekly-report
#   - 新对话 + 切到 catfish-public-qwen-flash, 发指令测试

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

CATFISH_ROOT="$HOME/person_task/catfish"
HERMES_VENV_PIP="$HOME/.hermes/hermes-agent/venv/bin/pip"
HERMES_VENV_PY="$HOME/.hermes/hermes-agent/venv/bin/python"

echo "============================================"
echo "🐟 catfish B 方案迁移 (4 步)"
echo "============================================"
echo ""

# ── 1. unset proxy ────────────────────────────
echo -e "${YELLOW}[1/4] 清理代理 env...${RESET}"
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy 2>/dev/null || true
echo -e "${GREEN}✓ proxy 已清${RESET}"
echo ""

# ── 2. hermes venv 装依赖 ─────────────────────
echo -e "${YELLOW}[2/4] hermes venv 装 python-docx + openpyxl (清华镜像)...${RESET}"
if [ ! -x "$HERMES_VENV_PIP" ]; then
  echo -e "${RED}✗ hermes venv pip 不存在: $HERMES_VENV_PIP${RESET}"
  echo "    hermes 没装或路径不对, 退出."
  exit 1
fi

"$HERMES_VENV_PIP" install -q -i https://pypi.tuna.tsinghua.edu.cn/simple python-docx openpyxl 2>&1 | tail -5

# 验证
if "$HERMES_VENV_PY" -c "import docx; import openpyxl" 2>/dev/null; then
  echo -e "${GREEN}✓ python-docx + openpyxl 装好${RESET}"
else
  echo -e "${RED}✗ 装失败 — 重试: $HERMES_VENV_PIP install python-docx openpyxl${RESET}"
  exit 1
fi
echo ""

# ── 3. install_to_hermes.sh ────────────────────
echo -e "${YELLOW}[3/4] 同步 catfish skill 到 hermes 路径...${RESET}"
if [ ! -f "$CATFISH_ROOT/skills/install_to_hermes.sh" ]; then
  echo -e "${RED}✗ install_to_hermes.sh 不存在${RESET}"
  exit 1
fi

bash "$CATFISH_ROOT/skills/install_to_hermes.sh"
echo ""

# ── 4. 杀 gateway + tool-bridge ────────────────
echo -e "${YELLOW}[4/4] 杀 gateway + tool-bridge (watchdog 会拉新的)...${RESET}"

# gateway
gateway_pid=$(lsof -i:8999 2>/dev/null | grep Python | awk '{print $2}' | head -1 || true)
if [ -n "$gateway_pid" ]; then
  kill -9 "$gateway_pid" 2>/dev/null || true
  echo "  - gateway PID $gateway_pid 已杀"
fi

# tool-bridge
pkill -9 -f catfish_tool_bridge 2>/dev/null || true
echo "  - tool-bridge 已杀"

sleep 5

# 重启 gateway (后台)
cd "$CATFISH_ROOT/central/llm-gateway"
nohup python -m catfish_gateway.app > /tmp/catfish-gateway.log 2>&1 &
gateway_new_pid=$!
echo "  - gateway 重启 PID $gateway_new_pid (log: /tmp/catfish-gateway.log)"

sleep 8

# tool-bridge 应该被 Companion watchdog 拉起来
tool_bridge_pid=$(pgrep -f catfish_tool_bridge | head -1 || true)
if [ -n "$tool_bridge_pid" ]; then
  echo -e "  ${GREEN}✓ tool-bridge 重启 PID $tool_bridge_pid${RESET}"
else
  echo -e "  ${YELLOW}⚠ tool-bridge 还没起来 — Companion 没开? 手动启动 Companion${RESET}"
fi
echo ""

# ── 验证 ─────────────────────────────────────
echo "============================================"
echo -e "${GREEN}✓ 全部完成${RESET}"
echo "============================================"
echo ""
echo "验证 hermes 看到 catfish skill:"
ls -la "$HOME/.hermes/skills/productivity/" 2>/dev/null | grep catfish | head -10
echo ""
echo "下一步:"
echo "  1. Companion 窗口 Cmd+R 刷新"
echo "  2. + 新对话"
echo "  3. 切 model → catfish-public-qwen-flash (不要主力 122b)"
echo "  4. 发: 写一份本周周报"
echo "  5. 看模型出 catfish-weekly-report 工具调用 + 桌面真有 .xlsx"
