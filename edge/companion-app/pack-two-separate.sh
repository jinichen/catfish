#!/usr/bin/env bash
# BL-TWO-SEPARATE-PACKS (7/19 15:26 鸿波 catch "分别编译不要混一起"):
# arm64 + x64 · 两个独立 tar.gz · 各自 install-mac.sh · 达华 IT 按员工 mac 架构选包.

set -uo pipefail

BUILD_DIR=~/person_task/catfish/edge/companion-app/src-tauri/target
TS=$(date +%Y%m%d-%H%M)

echo "════════════════════════════════════════════"
echo " 两平台分别打包 · $TS"
echo "════════════════════════════════════════════"

# ─── verify 都在 ─────
ARM64_APP="$BUILD_DIR/aarch64-apple-darwin/release/bundle/macos/Catfish Companion.app"
X64_APP="$BUILD_DIR/x86_64-apple-darwin/release/bundle/macos/Catfish Companion.app"

for platform in "arm64:$ARM64_APP" "x64:$X64_APP"; do
    name=${platform%%:*}
    app=${platform#*:}
    if [[ ! -d "$app" ]]; then
        echo "❌ $name .app 不在: $app"
        echo "  → build 未完成 · 等 tauri build 跑完再打"
        exit 1
    fi
    echo "✓ $name: $app · $(du -sh "$app" | awk '{print $1}')"
done

# ─── 独立打 arm64 ─────
pack_platform() {
    local name=$1
    local app=$2
    local staging=/tmp/catfish-dahua-$name-$TS
    local out=~/catfish-companion-dahua-$name-$TS.tar.gz

    echo ""
    echo "→ 打 $name 包..."
    rm -rf "$staging"
    mkdir -p "$staging"

    # cp .app (rename with arch 明确)
    cp -R "$app" "$staging/"

    # plugin.py 也带 (Task #22/#25)
    cp ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/plugin.py "$staging/plugin-latest.py"

    # install-mac.sh (arch-specific)
    cat > "$staging/install-mac.sh" <<SHEOF
#!/usr/bin/env bash
# Catfish Companion 达华 POC · $name 版 · $TS
set -euo pipefail
cd "\$(dirname "\$0")"

EXPECTED_ARCH="$name"
if [[ "\$EXPECTED_ARCH" == "arm64" ]]; then
    NEED_ARCH="arm64"
elif [[ "\$EXPECTED_ARCH" == "x64" ]]; then
    NEED_ARCH="x86_64"
fi
ACTUAL_ARCH=\$(uname -m)
if [[ "\$ACTUAL_ARCH" != "\$NEED_ARCH" ]]; then
    echo "❌ 架构不匹配 · 期望 \$NEED_ARCH · 实际 \$ACTUAL_ARCH · 请用另一个包" >&2
    exit 1
fi

echo "✓ \$ACTUAL_ARCH · 装机 (Catfish Companion \$EXPECTED_ARCH)..."

osascript -e 'quit app "Catfish Companion"' 2>/dev/null || true
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

if [[ -d "/Applications/Catfish Companion.app" ]]; then
    rm -rf "/Applications/Catfish Companion.app"
fi
cp -R "Catfish Companion.app" /Applications/
xattr -cr "/Applications/Catfish Companion.app"

# Task #22 plugin.py cp
if [[ -d "\$HOME/.hermes/plugins/catfish-xcatfish-user" ]]; then
    cp plugin-latest.py "\$HOME/.hermes/plugins/catfish-xcatfish-user/plugin.py"
    find "\$HOME/.hermes/plugins/catfish-xcatfish-user" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
fi

# Task #24 sed enabled=true
YAML="\$HOME/.catfish/companion.yaml"
if [[ -f "\$YAML" ]] && grep -q "^  enabled: false" "\$YAML"; then
    sed -i '' 's/^  enabled: false/  enabled: true/' "\$YAML"
fi

# Task #27 · 拿 30 天 service token · 双塞 env
IDENTITY_URL="\${CATFISH_IDENTITY_URL:-http://127.0.0.1:8998}"
JWT_RESP=\$(curl -sS -X POST "\$IDENTITY_URL/token" \\
    -H "Content-Type: application/x-www-form-urlencoded" \\
    --data-urlencode "grant_type=client_credentials" \\
    --data-urlencode "client_id=hermes-cli" \\
    --data-urlencode "client_secret=hermes-dev-secret-2026-please-change" \\
    --data-urlencode "scope=chat.completions" 2>/dev/null)
NEW_JWT=\$(echo "\$JWT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
if [[ -n "\$NEW_JWT" && -f "\$HOME/.hermes/.env" ]]; then
    python3 -c "
import re
p = '\$HOME/.hermes/.env'
with open(p) as f: content = f.read()
for key in ['OPENAI_API_KEY', 'HERMES_SERVICE_TOKEN']:
    if f'{key}=' in content:
        content = re.sub(rf'^{key}=.*\\\$', f'{key}=\$NEW_JWT', content, count=1, flags=re.M)
    else:
        content = content.rstrip() + f'\\n{key}=\$NEW_JWT\\n'
with open(p, 'w') as f: f.write(content)
"
    chmod 600 "\$HOME/.hermes/.env"
    echo "  ✓ hermes/.env 双 key sync (30 天)"

    export PATH="\$HOME/.hermes/hermes-agent/venv/bin:\$PATH"
    hermes gateway restart 2>&1 | tail -1 || true
fi

open -a "Catfish Companion"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 装完 · 首启装 hermes-agent (10-15 min · 只此一次 · 别关)"
echo "════════════════════════════════════════════"
SHEOF
    chmod +x "$staging/install-mac.sh"

    # README
    cat > "$staging/README.md" <<MDEOF
# Catfish Companion 达华 POC · $name 版 · $TS

**架构**: $name ($([ "$name" = "arm64" ] && echo "Apple Silicon M1/M2/M3/M4" || echo "Intel"))

## 装机
\`\`\`bash
bash install-mac.sh
\`\`\`

## 首启后
1. 等 hermes-agent 装 (10-15 min)
2. 面板 → 服务器配置 → 输达华 URL · 保存 · SSO 登录
3. 手机 WeChat 扫码绑 · 发 hi 测

## 7/19 全 fix 版本
- JWT 双 token 自动 refresh (env 30 天 service + config.yaml 1h access)
- WeChat 命令 (/批准 / /批准 本次会话 / /拒绝) 无条件识别
- Companion Chat approval 按钮弹 · execute_code 单次批
- WeChat /批准 本次会话 后 · 后续同 pattern execute_code auto-approve
- advisor LLM 返 null 时 · 中性提示不阻塞早安数据统计

## 若装完撞 401
指定达华 identity URL · 重跑:
\`\`\`bash
CATFISH_IDENTITY_URL=http://<达华 identity ip>:8998 bash install-mac.sh
\`\`\`
MDEOF

    # tar.gz
    cd /tmp
    tar -czf "$out" "$(basename $staging)"
    local size=$(ls -lh "$out" | awk '{print $5}')
    echo "  ✓ $out · $size"
}

pack_platform "arm64" "$ARM64_APP"
pack_platform "x64" "$X64_APP"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 两个独立分发包 · 达华 IT 按员工 mac 架构选"
echo "════════════════════════════════════════════"
ls -lh ~/catfish-companion-dahua-{arm64,x64}-$TS.tar.gz 2>/dev/null

echo ""
echo "→ scp:"
echo "  scp ~/catfish-companion-dahua-arm64-$TS.tar.gz dahua@<ip>:/tmp/  # M-series 员工"
echo "  scp ~/catfish-companion-dahua-x64-$TS.tar.gz   dahua@<ip>:/tmp/  # Intel 员工"
