#!/usr/bin/env bash
# BL-FINAL-CLEAN-PACK (7/19 13:57): 只保 /Applications · 删其他 4 个 · 打分发.


# ─── 已废弃 (8/1) ────────────────────────────────────────────────
#
# 这是 7/19 那天为一次具体分发临时写的脚本 (看上面的注释, 精确到分钟),
# 里面写死了 `Catfish Companion_0.18.0_*.dmg` —— 那些文件今天不存在,
# 现在是 0.20.0, 而且版本号跟 hermes 钉死 (见 check_version_sync.sh)。
#
# 照它跑不会报错, 只会拷不到文件然后打出误导性的提示 —— 而这类脚本恰恰是
# 半年后有人翻出来"看着像能用"就直接跑的那种。
#
# 现在的正确做法:
#   bash scripts/build-mac-resources.sh aarch64   # 或 x64
#   npm run tauri:build:arm64                     # 或 tauri:build:x64
#
# 保留本文件只为记录这段历史 (跟 scripts/build-intel-dmg.sh 同一个处理)。
exit_deprecated() {
    echo "❌ 本脚本已废弃 (7/19 一次性分发脚本, 写死 0.18.0 的文件名)"
    echo "   现在用: bash scripts/build-mac-resources.sh <arch> && npm run tauri:build:<arch>"
    exit 1
}
exit_deprecated

set -uo pipefail

SOURCE_APP=/Applications/Catfish\ Companion.app
BIN="$SOURCE_APP/Contents/MacOS/catfish-companion-app"

echo "════════════════════════════════════════════"
echo " 清 5 个 .app · 只保 /Applications · 打分发"
echo "════════════════════════════════════════════"

# 1. verify /Applications 是最新版
echo ""
echo "→ [1/6] verify /Applications 版本 (期望 · 3 处 fix 全在)"
if [[ ! -d "$SOURCE_APP" ]]; then
    echo "  ❌ /Applications/Catfish Companion.app 不在 · 需先装:"
    echo "    cp -R ~/person_task/catfish/edge/companion-app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/'Catfish Companion.app' /Applications/"
    exit 1
fi

P22=$(strings "$BIN" 2>/dev/null | grep -c "BL-P14-SLASH-UNCONDITIONAL")
P23=$(strings "$BIN" 2>/dev/null | grep -c "BL-P27-STATUS-PENDING-FIX")
P24=$(strings "$BIN" 2>/dev/null | grep -c "BL-HERMES-DEFAULT-ENABLED")
JWT=$(strings "$BIN" 2>/dev/null | grep -c "hermes-jwt-sync")

echo "  Task #22 P14-uncond: $P22 (期望 >= 1)"
echo "  Task #23 P27 UI:     $P23 (期望 >= 1)"
echo "  Task #24 default en: $P24 (期望 >= 1)"
echo "  Task #15 jwt_sync:   $JWT (期望 >= 5)"

if [[ "$P22" == "0" || "$P23" == "0" || "$P24" == "0" || "$JWT" == "0" ]]; then
    echo ""
    echo "  ⚠ /Applications 里的不是最新 build · 先更新:"
    echo "    rm -rf /Applications/'Catfish Companion.app'"
    echo "    cp -R ~/person_task/catfish/edge/companion-app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/'Catfish Companion.app' /Applications/"
    echo "    xattr -cr /Applications/'Catfish Companion.app'"
    echo "    再跑本脚本"
    exit 1
fi

# 2. 关掉 Companion (若在跑)
echo ""
echo "→ [2/6] 关 Companion"
osascript -e 'quit app "Catfish Companion"' 2>/dev/null || true
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

# 3. 删所有别的 .app (保留 /Applications)
echo ""
echo "→ [3/6] 删除 · 除 /Applications 外的所有 Catfish Companion.app"
mdfind -name "Catfish Companion.app" 2>/dev/null | while IFS= read -r app; do
    if [[ "$app" != "/Applications/Catfish Companion.app" ]]; then
        echo "  删 $app"
        rm -rf "$app" 2>/dev/null || true
    fi
done

# 4. 强制刷 Spotlight index
echo ""
echo "→ [4/6] 刷 Spotlight index (清 Spotlight cache)"
mdimport /Applications/Catfish\ Companion.app 2>/dev/null || true

# 5. 打分发 tar.gz
echo ""
echo "→ [5/6] 打分发 tar.gz · 只 arm64 (M-series mac)"
TS=$(date +%Y%m%d-%H%M)
STAGING=/tmp/dahua-clean-$TS
OUT=~/catfish-companion-dahua-arm64-CLEAN-$TS.tar.gz

rm -rf "$STAGING"
mkdir -p "$STAGING/arm64"
cp -R "$SOURCE_APP" "$STAGING/arm64/"
cp ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/plugin.py "$STAGING/plugin-p14-uncond.py"

cat > "$STAGING/install-mac.sh" <<'SHEOF'
#!/usr/bin/env bash
# Catfish Companion 达华 POC v0.18.0 · 7/19 CLEAN · 一键装机
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "❌ 需 Apple Silicon mac (M1/M2/M3/M4)" >&2
    exit 1
fi

echo "✓ arm64 检测 · 装机..."

# 关老 Companion
osascript -e 'quit app "Catfish Companion"' 2>/dev/null || true
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

# 清老版本
if [[ -d "/Applications/Catfish Companion.app" ]]; then
    rm -rf "/Applications/Catfish Companion.app"
fi

# cp 新 .app
cp -R "arm64/Catfish Companion.app" /Applications/
xattr -cr "/Applications/Catfish Companion.app"

# 装 plugin.py Task #22 (Companion 首启会 overwrite · 但 include_str baked 也是新版)
if [[ -d "$HOME/.hermes/plugins/catfish-xcatfish-user" ]]; then
    cp plugin-p14-uncond.py "$HOME/.hermes/plugins/catfish-xcatfish-user/plugin.py"
    find "$HOME/.hermes/plugins/catfish-xcatfish-user" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
fi

# sed companion.yaml hermes_api.enabled true (Task #24)
YAML="$HOME/.catfish/companion.yaml"
if [[ -f "$YAML" ]] && grep -q "^  enabled: false" "$YAML"; then
    sed -i '' 's/^  enabled: false/  enabled: true/' "$YAML"
fi

# BL-P27-DUAL-KEY-INSTALL (7/19 Task #27): 拿 30 天 service token 双塞 ~/.hermes/.env
# OPENAI_API_KEY (credential_pool) + HERMES_SERVICE_TOKEN (P7 proxy). 员工装完
# 立即 30 天不撞 401. Companion 后台每 25 天定时续.
IDENTITY_URL="${CATFISH_IDENTITY_URL:-http://127.0.0.1:8998}"
echo "→ 拿 30 天 service token (identity=$IDENTITY_URL)..."
JWT_RESP=$(curl -sS -X POST "$IDENTITY_URL/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=hermes-cli" \
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \
    --data-urlencode "scope=chat.completions" 2>/dev/null)
NEW_JWT=$(echo "$JWT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [[ -n "$NEW_JWT" && -f "$HOME/.hermes/.env" ]]; then
    python3 -c "
import re
p = '$HOME/.hermes/.env'
with open(p) as f: content = f.read()
for key in ['OPENAI_API_KEY', 'HERMES_SERVICE_TOKEN']:
    if f'{key}=' in content:
        content = re.sub(rf'^{key}=.*\$', f'{key}=$NEW_JWT', content, count=1, flags=re.M)
    else:
        content = content.rstrip() + f'\n{key}=$NEW_JWT\n'
with open(p, 'w') as f: f.write(content)
"
    chmod 600 "$HOME/.hermes/.env"
    echo "  ✓ hermes/.env OPENAI_API_KEY + HERMES_SERVICE_TOKEN 双塞完 (30 天)"

    # restart hermes 让新 env 生效
    if command -v hermes >/dev/null 2>&1; then
        export PATH="$HOME/.hermes/hermes-agent/venv/bin:$PATH"
        hermes gateway restart 2>&1 | tail -1
    fi
else
    echo "  ⚠ identity URL 不通 · 员工首次登录 Companion 时会自动 refresh · POC 应该没问题"
    echo "     若撞 401 · 手工跑 CATFISH_IDENTITY_URL=<达华 identity URL> bash install-mac.sh"
fi

open -a "Catfish Companion"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 装完 · 首启会自动装 hermes-agent (10-15min · 只此一次)"
echo "════════════════════════════════════════════"
echo "面板 → 服务器配置 → 输达华 URL · 保存 · SSO 登录"
SHEOF
chmod +x "$STAGING/install-mac.sh"

cat > "$STAGING/README.md" <<'MDEOF'
# Catfish Companion 达华 POC v0.18.0 · 7/19

## 装机
```bash
bash install-mac.sh
```

## 首启 3 步
1. 等 hermes-agent 装 (10-15min · 只此一次)
2. 面板 → 服务器配置 → 输达华 URL · 保存 · SSO 登录
3. 手机 WeChat 扫码绑

## 7/19 关键 fix
- JWT 自动 refresh (每 25min · 员工无感)
- WeChat 中文命令 (/批准 / /批准 本次会话 / /拒绝) 无条件识别
- Companion Chat approval 按钮走 hermes 8642 (yaml auto set)
- catfish-auto 动态 model routing

## execute_code 员工用法
每次 execute_code 需 `/批准` 单次批准. `本次会话` 免批仍在 POC 后深追.

## 出问题
截图 · 面板服务器配置 + WeChat 错误 + `tail -50 ~/catfish-gateway-$(date +%Y%m%d).log`
MDEOF

cd /tmp
tar -czf "$OUT" "$(basename $STAGING)"

# 6. verify
echo ""
echo "→ [6/6] verify tar 目录"
tar -tzf "$OUT" | head -10
SIZE=$(ls -lh "$OUT" | awk '{print $5}')

echo ""
echo "════════════════════════════════════════════"
echo "✅ 分发包 · $OUT · $SIZE"
echo "════════════════════════════════════════════"
echo ""
echo "→ scp 到达华:"
echo "  scp $OUT dahua@<ip>:/tmp/"
echo ""
echo "→ Spotlight 搜 'cat' 现在应只出 1 个 (/Applications 里的)"
echo "  等 10 秒 Spotlight cache 刷 · 若还多 · 手工搜: mdfind -name 'Catfish Companion.app'"
