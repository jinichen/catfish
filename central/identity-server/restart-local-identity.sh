#!/usr/bin/env bash
# BL-LOCAL-IDENTITY-RESTART (7/20 Task #29): 本地 identity restart · 用 venv/bin/python.
#
# 军规坑: README 写 `PYTHONPATH=src python3 -m catfish_identity` · 假设系统
# python 已装 fastapi. 但用户系统 python3.14 没装 · venv 里才有. 该走 venv.
#
# 用法:
#   bash ~/person_task/catfish/central/identity-server/restart-local-identity.sh
#
# 做:
#   1. verify · venv 有 catfish_identity + fastapi + jwt
#   2. kill 8998 残留
#   3. nohup 后台起 · log 落 ~/catfish-identity-YYYYMMDD.log
#   4. verify · 8998 pid + /me/password endpoint 挂上

set -uo pipefail

ID_PY=~/person_task/catfish/central/identity-server/venv/bin/python
ID_DIR=~/person_task/catfish/central/identity-server
LOG=~/catfish-identity-$(date +%Y%m%d).log

echo "════════════════════════════════════════════"
echo " 本地 identity restart · Task #29"
echo "════════════════════════════════════════════"

# 1. verify venv 有必需 module
echo ""
echo "→ [1/4] verify venv..."
if ! $ID_PY -c "import catfish_identity, fastapi, jwt, bcrypt; print('  module OK')" 2>&1; then
    echo "❌ venv 挂 · 缺 module. 补装:"
    echo "   $ID_PY -m pip install fastapi 'uvicorn[standard]' PyJWT bcrypt asyncpg"
    exit 1
fi

# 2. kill 8998 残留 (SIGTERM 优雅 · 再 SIGKILL 强)
echo ""
echo "→ [2/4] kill 8998 残留..."
lsof -ti :8998 2>/dev/null | xargs -r kill -TERM
sleep 2
lsof -ti :8998 2>/dev/null | xargs -r kill -9

# 3. nohup 起
echo ""
echo "→ [3/4] 起 identity (venv/bin/python)..."
cd $ID_DIR
nohup env PYTHONPATH=src $ID_PY -m catfish_identity > $LOG 2>&1 &
disown
sleep 3

# 4. verify · pid + endpoint
echo ""
echo "→ [4/4] verify..."
PID=$(lsof -ti :8998 2>/dev/null | head -1)
if [ -z "$PID" ]; then
    echo "❌ 8998 未起 · log 尾:"
    tail -40 $LOG
    exit 1
fi
echo "  pid=$PID"

echo "  endpoint 挂了没:"
curl -s http://127.0.0.1:8998/openapi.json | $ID_PY -c "
import sys, json
try:
    paths = json.load(sys.stdin).get('paths', {})
except Exception as e:
    print(f'  ❌ openapi.json parse 失败: {e}')
    sys.exit(1)
pw = [p for p in paths if 'password' in p]
print(f'   password 相关 paths: {pw}')
assert '/me/password' in pw, '❌ /me/password 未挂 · 代码没载入'
print('  ✅ /me/password 已挂')
"

echo ""
echo "════════════════════════════════════════════"
echo " OK · log: $LOG"
echo " pid=$PID · lsof -i :8998 看进程"
echo "════════════════════════════════════════════"
