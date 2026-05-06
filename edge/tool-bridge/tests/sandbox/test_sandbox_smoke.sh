#!/usr/bin/env bash
# =====================================================================
# test_sandbox_smoke.sh — 验证 macOS sandbox-exec 自身能用
#
# 跑 3 个递增复杂度的 inline profile, 找到底是哪一步 sandbox-exec 卡住.
# 如果 1 都跑不通 → macOS 系统级 sandbox-exec 被禁 (SIP/TCC 之类), 不是 catfish profile 的问题.
# 如果 1 通了, 2 不通 → param/subpath 写法问题.
# 如果 2 通了, 3 不通 → string-append 或某动词 SBPL 1 不支持.
# =====================================================================

set -u

TASK_DIR=$(mktemp -d /tmp/catfish-smoke-XXXXXX)
trap 'rm -rf "$TASK_DIR"' EXIT

echo "========== sandbox-exec 烟雾测试 (找根因) =========="
echo

# Test 1: 最简 profile, 只 deny network — 验 sandbox-exec 自身能跑 python
echo "[Test 1] 最简 profile (只 deny network*)"
out=$(sandbox-exec -p '(version 1)(deny network*)' \
        /usr/bin/python3 -c 'print("hello-from-sandbox")' 2>&1)
rc=$?
echo "  rc=$rc"
echo "  output: $out"
if [[ $rc -eq 0 && "$out" == "hello-from-sandbox" ]]; then
  echo "  ✅ Test 1 通: sandbox-exec 自身 OK"
else
  echo "  ❌ Test 1 失败: sandbox-exec 系统级被禁? 看下面诊断"
  echo
  echo "  诊断: 跑 sandbox-exec --help"
  /usr/sbin/sandbox-exec 2>&1 | head -3
  echo
  echo "  诊断: 看是否被 TCC / SIP 限制"
  csrutil status 2>/dev/null || echo "    (csrutil 不可用)"
  exit 1
fi
echo

# Test 2: 带 -D param + subpath 写法
echo "[Test 2] -D param + subpath 写法"
out=$(sandbox-exec -p "(version 1)(deny network*)(allow file-read* (subpath (param \"TD\")))" \
        -D "TD=$TASK_DIR" \
        /usr/bin/python3 -c 'print("hello-with-param")' 2>&1)
rc=$?
echo "  rc=$rc"
echo "  output: $out"
if [[ $rc -eq 0 && "$out" == "hello-with-param" ]]; then
  echo "  ✅ Test 2 通: -D param + subpath 写法 OK"
else
  echo "  ❌ Test 2 失败: -D / subpath / param 写法有问题"
  exit 1
fi
echo

# Test 3: string-append 拼路径 (SBPL 1 不一定支持)
echo "[Test 3] string-append 拼路径"
out=$(sandbox-exec -p "(version 1)(deny network*)(deny file-read* (subpath (string-append (param \"H\") \"/.ssh\")))" \
        -D "H=$HOME" \
        /usr/bin/python3 -c 'print("hello-with-string-append")' 2>&1)
rc=$?
echo "  rc=$rc"
echo "  output: $out"
if [[ $rc -eq 0 && "$out" == "hello-with-string-append" ]]; then
  echo "  ✅ Test 3 通: string-append 在 SBPL 1 可用"
else
  echo "  ❌ Test 3 失败: string-append 在 macOS $(sw_vers -productVersion) 上 SBPL 1 不支持"
  echo "  → 要改用直接传完整路径 -D HOME_SSH=\$HOME/.ssh"
  exit 1
fi
echo

# Test 4: device-camera (SBPL 5+ 嫌疑动词)
echo "[Test 4] device-camera 动词 (SBPL 5+ 嫌疑)"
out=$(sandbox-exec -p '(version 1)(deny device-camera)' \
        /usr/bin/python3 -c 'print("hello-device-camera")' 2>&1)
rc=$?
echo "  rc=$rc"
echo "  output: $out"
if [[ $rc -eq 0 ]]; then
  echo "  ✅ Test 4 通: device-camera SBPL 1 支持"
else
  echo "  ❌ Test 4 失败: device-camera 是 SBPL 5+ 私有, 要从 profile 删"
fi
echo

# Test 5: mach-priv-host-port (SBPL 5+ 嫌疑动词)
echo "[Test 5] mach-priv-host-port 动词 (SBPL 5+ 嫌疑)"
out=$(sandbox-exec -p '(version 1)(deny mach-priv-host-port)' \
        /usr/bin/python3 -c 'print("hello-mach-priv")' 2>&1)
rc=$?
echo "  rc=$rc"
echo "  output: $out"
if [[ $rc -eq 0 ]]; then
  echo "  ✅ Test 5 通: mach-priv-host-port SBPL 1 支持"
else
  echo "  ❌ Test 5 失败: mach-priv-host-port 是 SBPL 5+ 私有, 要从 profile 删"
fi
echo

echo "========== 烟雾测试完成 =========="
echo "macOS 版本: $(sw_vers -productVersion)"
echo "把上面输出贴给鸿波 / Claude, 帮定位真正出错的动词"
