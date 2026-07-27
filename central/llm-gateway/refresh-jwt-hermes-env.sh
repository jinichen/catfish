#!/usr/bin/env bash
# BL-HERMES-ENV-JWT-REFRESH (7/19): 军规漏!
# refresh-jwt-and-restart.sh 只改了 ~/.hermes/config.yaml · 没改 ~/.hermes/.env OPENAI_API_KEY.
# hermes credential_pool 从 env:OPENAI_API_KEY 拿 · config.yaml 只喂 model 段 · 分裂路径.
# 这次一次修完 · 且 reset auth.json exhausted 状态.
#
# BL-PLUGIN-AUTH-FIX (7/27 鸿波): scope 加 background.tasks.
#   hermes 进程内 plugin (catfish-memory distill/summarize · catfish-xcatfish-user
#   memory_enforce) 复用本脚本写的 OPENAI_API_KEY 调 gateway. gateway 侧
#   app.py is_internal_call 校验这个 scope 才允许免 quota (堵 header 滥用洞).
#   没这 scope → plugin 调用照常扣员工 quota (功能不断 · 只是记账变了).
#   ⚠ 前置: identity-server clients.yaml 的 hermes-cli 必须先有 background.tasks
#      在 allowed_scopes 里 (不然 /token 返 invalid_scope), 且 identity 要重启.

set -uo pipefail

echo "════════════════════════════════════════════"
echo " Refresh hermes/.env OPENAI_API_KEY · 7/19"
echo "════════════════════════════════════════════"

# ─── [1/4] 拿 30 天 service JWT ─────────────
echo ""
echo "→ [1/4] 拿 30 天 service JWT (hermes-cli client_credentials)..."
NEW_JWT=$(curl -sS -X POST http://127.0.0.1:8998/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=hermes-cli" \
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \
    --data-urlencode "scope=chat.completions background.tasks" \
    2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [[ -z "$NEW_JWT" ]]; then
    echo "❌ identity 8998 挂 · 拿不到 JWT"
    exit 1
fi
echo "  ✓ JWT 拿到 · 长度=${#NEW_JWT}"

# ─── [2/4] sed 塞 ~/.hermes/.env OPENAI_API_KEY ─────────────
echo ""
echo "→ [2/4] sed ~/.hermes/.env OPENAI_API_KEY..."
cp ~/.hermes/.env ~/.hermes/.env.bak-$(date +%s)
# BL-REFRESH-JWT-YAML-FIX (7/27): 老代码 count=1 只替换第一个 OPENAI_API_KEY=,
# 若文件里有重复 key (历史误操作 / 手工加过) 后面的残留, hermes load_env() 拿哪个
# 取决于 parse 顺序 → 可能用到旧 token. 改: 删所有旧行 + 在原位置插新的 (保 diff 友好),
# 完事 verify 只剩 1 个.
python3 <<PYEOF
import sys
p = '/Users/chenhongbo/.hermes/.env'
with open(p) as f:
    lines = f.read().splitlines()

KEY = 'OPENAI_API_KEY='
idxs = [i for i, l in enumerate(lines) if l.strip().startswith(KEY)]
if len(idxs) > 1:
    print(f"  ⚠ 检出 {len(idxs)} 个 {KEY} 行 (重复 key) → 只保留 1 个")

new_line = KEY + '$NEW_JWT'
if idxs:
    lines[idxs[0]] = new_line
    # 倒序删多余的 (防 index 偏移)
    for i in reversed(idxs[1:]):
        del lines[i]
else:
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(new_line)

with open(p, 'w') as f:
    f.write('\n'.join(lines) + '\n')

# verify
with open(p) as f:
    n = sum(1 for l in f if l.strip().startswith(KEY))
if n != 1:
    print(f"  ❌ verify 失败 · .env 里有 {n} 个 {KEY} (期望 1) — 手工检查 {p}")
    sys.exit(1)
print('  ✓ OPENAI_API_KEY 替换/追加完 (verify: 1 行)')
PYEOF

# 同步 · config.yaml api_key (保险 · 双写)
#
# BL-REFRESH-JWT-YAML-FIX (7/27 鸿波实测撞): 老代码用 regex 改, 写坏过 config.yaml.
#
# 老代码:  re.sub(r'^(  api_key:\s*).*$', ..., count=1, flags=re.M)
#   `.*$` 只吃**一行** + count=1 只替换 1 次. 一旦 api_key 变成多行 (YAML plain
#   scalar 续行), 就只替换第一行, 缩进的旧 JWT 残留:
#       model:
#         api_key: <新 JWT>
#           <旧 JWT · 已过期>     ← 残留
#   YAML 解析后 api_key = "新JWT 旧JWT" 两个拼一起 → hermes 拿到废 token.
#   而且**这 bug 自我延续** — 下次刷新还是只改第一行, 永远修不回来.
#
# 修: 改用 yaml 库读写 (结构化, 不可能产生续行残留) + 改完 verify 只有 1 个 JWT.
#     config.yaml 零注释纯数据 (7/27 实测), safe_dump 重写零损失.
#     sort_keys=False 保持原 key 顺序, allow_unicode=True 防中文转义.
echo ""
echo "  → 同步 · config.yaml model.api_key (yaml 库结构化写)..."
python3 <<PYEOF
import sys
try:
    import yaml
except ImportError:
    print("  ❌ 缺 pyyaml — config.yaml 没同步 (但 .env 已更新, hermes 走 .env 也能跑)")
    print("     补: pip3 install pyyaml 后重跑本脚本")
    sys.exit(0)   # 不阻塞 · .env 是主路径

p = '/Users/chenhongbo/.hermes/config.yaml'
with open(p) as f:
    data = yaml.safe_load(f) or {}
if not isinstance(data, dict):
    print("  ❌ config.yaml 不是 dict, 跳过 (手工检查)")
    sys.exit(0)

model = data.get('model')
if not isinstance(model, dict):
    model = {}
    data['model'] = model

old = str(model.get('api_key') or '')
# fail-loud · 检出老多行残留 (老 regex bug 的后遗症)
if old.count('eyJhbGci') > 1:
    print(f"  ⚠ 检出 api_key 含 {old.count('eyJhbGci')} 个 JWT (老 regex bug 残留) → 本次一并清干净")

model['api_key'] = '$NEW_JWT'
with open(p, 'w') as f:
    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

# verify · 重读确认只有 1 个 JWT
with open(p) as f:
    check = yaml.safe_load(f)
n = str(check.get('model', {}).get('api_key') or '').count('eyJhbGci')
if n != 1:
    print(f"  ❌ verify 失败 · api_key 里有 {n} 个 JWT (期望 1) — 手工检查 {p}")
    sys.exit(1)
print('  ✓ config.yaml api_key 也同步 (verify: 1 个 JWT)')
PYEOF

# ─── [3/4] reset auth.json exhausted 状态 ─────────────
echo ""
echo "→ [3/4] reset ~/.hermes/auth.json openai-api 池 (last_status=exhausted 会拒调)..."
python3 <<PYEOF
import json
p = '/Users/chenhongbo/.hermes/auth.json'
try:
    with open(p) as f: d = json.load(f)
    reset_count = 0
    for cred in d.get('credential_pool', {}).get('openai-api', []):
        cred['last_status'] = 'healthy'
        cred['last_error_code'] = None
        cred['last_error_message'] = None
        cred['last_error_reset_at'] = None
        reset_count += 1
    with open(p, 'w') as f: json.dump(d, f, indent=2)
    print(f'  ✓ reset {reset_count} 个 openai-api cred 到 healthy')
except FileNotFoundError:
    print('  ⚠ auth.json 不存在 · 跳过 (hermes 首启会重建)')
except Exception as e:
    print(f'  ⚠ reset 挂 (不阻塞): {e}')
PYEOF

# ─── [4/4] hermes 冷启 (kill -9 + bootout + install --force + start) ─────────────
echo ""
echo "→ [4/4] hermes 8642 冷启..."
export PATH=~/.hermes/hermes-agent/venv/bin:$PATH

hermes gateway stop 2>&1 | tail -3
sleep 3
pkill -9 -f "hermes.*gateway" 2>/dev/null || true
sleep 2
launchctl bootout gui/$(id -u)/ai.hermes.gateway 2>/dev/null || true
hermes gateway install --force 2>&1 | tail -3
hermes gateway start 2>&1 | tail -3

# BL-REFRESH-JWT-WAIT-FIX (7/27 鸿波实测): 老代码 `sleep 5` 就查端口 → 误报
# "❌ hermes 8642 没起". 真实冷启耗时 (7/27 11:14 日志):
#   11:14:57.706  API server listening on 8642
#   11:15:20.463  Press Ctrl+C to stop  ← 真就绪, 距 start 约 23s
# 5s 时连 listening 都可能没到. 改**轮询到 45s**, 每 2s 查一次, 起来就早退.
echo "  → 等 hermes 8642 起 (冷启约 20-30s, 最多等 45s)..."
NEW_PID=""
for i in $(seq 1 23); do
    NEW_PID=$(lsof -ti:8642 -sTCP:LISTEN 2>/dev/null | head -1)
    [[ -n "$NEW_PID" ]] && break
    sleep 2
done

if [[ -n "$NEW_PID" ]]; then
    echo ""
    echo "════════════════════════════════════════════"
    echo "✅ 全修完 · hermes 8642 pid=$NEW_PID (等了 $((i * 2))s)"
    echo "════════════════════════════════════════════"
    ps -p $NEW_PID -o pid,etime,command | head -2
    echo ""
    echo "→ 手机 WeChat 发 hi · 期望: DeepSeek 中文回复"
    echo ""
    echo "→ 若还英文 · tail 抓 log:"
    echo "  tail -20 ~/catfish-gateway-\$(date +%Y%m%d).log | grep -E 'chat|401|200|exp|sub'"
else
    echo "❌ hermes 8642 等 45s 还没起 · 手工排查:"
    echo "  hermes gateway status"
    echo "  tail -40 ~/.hermes/logs/gateway.log"
    echo "  tail -20 ~/.hermes/logs/gateway.error.log"
    echo ""
    echo "  (注 · 先看 gateway.log 有没有 'API server listening on 8642' —"
    echo "   有就是还在起, 再等等; 没有才是真挂)"
fi
