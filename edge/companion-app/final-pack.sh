#!/usr/bin/env bash
# BL-FINAL-PACK (7/19 12:47): 用 · 12:40 arm64 build · + 12:10 x64 build · 打分发.
# 免 zsh 粘贴挂.


# ─── 已废弃 (8/1) ────────────────────────────────────────────────
#
# 这是 7/19 那天为一次具体分发临时写的脚本 (看上面的注释, 精确到分钟),
# 里面写死了 `Catfish Companion_0.18.0_*.dmg` —— 那些文件今天不存在,
# 现在是 0.19.0, 而且版本号跟 hermes 钉死 (见 check_version_sync.sh)。
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

TS=$(date +%Y%m%d)
STAGING=/tmp/dahua-final-$TS
OUT=~/catfish-companion-dahua-$TS-FINAL.tar.gz

echo "════════════════════════════════════════════"
echo " 达华 POC 最终打包 · $TS"
echo "════════════════════════════════════════════"

# arm64 · target 里的 build (最新)
ARM64_APP=~/person_task/catfish/edge/companion-app/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Catfish\ Companion.app

# x64 · fallback 3 处 · target 优先 · 再 · /Applications · 再老 dahua-20260719
X64_CANDIDATES=(
    ~/person_task/catfish/edge/companion-app/src-tauri/target/x86_64-apple-darwin/release/bundle/macos/Catfish\ Companion.app
    ~/catfish-dahua-20260719/x64/Catfish\ Companion.app
)
X64_APP=""
for cand in "${X64_CANDIDATES[@]}"; do
    if [[ -d "$cand" ]]; then
        X64_APP="$cand"
        break
    fi
done
if [[ -z "$X64_APP" ]]; then
    echo "  ❌ x64 .app 都不在 · 尝试 mdfind:"
    mdfind -name "Catfish Companion.app" 2>/dev/null | grep -i "x86_64\|x64" | head -3
    echo ""
    echo "  → 若你只需 arm64 (M-series mac) · 只打 arm64 · 手工跑:"
    echo "    ARM64_APP='$ARM64_APP'"
    echo "    mkdir -p /tmp/dahua-arm-only/arm64"
    echo "    cp -R \"\$ARM64_APP\" /tmp/dahua-arm-only/arm64/"
    echo "    cd /tmp && tar -czf ~/catfish-companion-dahua-arm64-only.tar.gz dahua-arm-only/"
    exit 1
fi

echo ""
echo "→ [1/4] verify · .app 都在 + P14 fix + jwt_sync"

for app_var in "arm64:$ARM64_APP" "x64:$X64_APP"; do
    arch=${app_var%%:*}
    app=${app_var#*:}
    if [[ ! -d "$app" ]]; then
        echo "  ❌ $arch .app 不在: $app"
        exit 1
    fi
    bin="$app/Contents/MacOS/catfish-companion-app"
    P14=$(strings "$bin" 2>/dev/null | grep -c "BL-P14-SLASH-ALIAS")
    P14U=$(strings "$bin" 2>/dev/null | grep -c "BL-P14-SLASH-UNCONDITIONAL")
    JWT=$(strings "$bin" 2>/dev/null | grep -c "hermes-jwt-sync")
    SIZE=$(du -sh "$app" | awk '{print $1}')
    echo "  ✓ $arch: $SIZE · P14=$P14 · P14-uncond=$P14U · jwt_sync=$JWT"
done

echo ""
echo "→ [2/4] staging"
rm -rf "$STAGING"
mkdir -p "$STAGING/arm64" "$STAGING/x64"
cp -R "$ARM64_APP" "$STAGING/arm64/"
cp -R "$X64_APP" "$STAGING/x64/"
echo "  ✓ 两 .app cp 完"

echo ""
echo "→ [3/4] 生成 install-mac.sh + QUICK-START.md + plugin.py 备份"
# plugin.py 直接带 · install-mac.sh 装完 也 cp 到 ~/.hermes/plugins/ 保 Task #22
cp ~/person_task/catfish/edge/hermes-plugins/catfish-xcatfish-user/plugin.py "$STAGING/plugin-p14-uncond.py"
echo "  ✓ plugin-p14-uncond.py (Task #22 · install-mac.sh 会 cp 到 ~/.hermes/plugins/)"

cat > "$STAGING/install-mac.sh" <<'SHEOF'
#!/usr/bin/env bash
# 达华员工 Mac 装 Companion · v0.18.0 · 7/19 FINAL
set -euo pipefail
cd "$(dirname "$0")"

ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    APP_DIR="arm64"
elif [[ "$ARCH" == "x86_64" ]]; then
    APP_DIR="x64"
else
    echo "❌ 未知架构: $ARCH" >&2
    exit 1
fi

APP="$APP_DIR/Catfish Companion.app"
if [[ ! -d "$APP" ]]; then
    echo "❌ $APP 不在 · 检查解压位置" >&2
    exit 1
fi

echo "✓ $ARCH · 装 $APP"

osascript -e 'quit app "Catfish Companion"' 2>/dev/null
sleep 3
pkill -9 -f "Catfish Companion" 2>/dev/null || true

if [[ -d "/Applications/Catfish Companion.app" ]]; then
    rm -rf "/Applications/Catfish Companion.app"
fi
cp -R "$APP" /Applications/
xattr -cr "/Applications/Catfish Companion.app"

# 若 Companion setup hook overwrite plugin.py · 我们再 cp 一次覆盖 · Task #22 生效
if [[ -f "plugin-p14-uncond.py" && -d "$HOME/.hermes/plugins/catfish-xcatfish-user" ]]; then
    cp plugin-p14-uncond.py "$HOME/.hermes/plugins/catfish-xcatfish-user/plugin.py"
    # 清 pyc · 强制 Python 用新 .py
    find "$HOME/.hermes/plugins/catfish-xcatfish-user" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo "  ✓ plugin.py Task #22 已 cp (会被 Companion 首启覆盖 · 但 include_str baked 也是新版)"
fi

# BL-COMPANION-YAML-HERMES-API-ENABLE (7/19 Task #24): 修达华员工老 companion.yaml
# 里 hermes_api.enabled: false · 让 Chat 走 hermes API server (8642) · P15
# approval SSE 生效 · Companion Chat 审批按钮弹.
YAML=~/.catfish/companion.yaml
if [[ -f "$YAML" ]] && grep -q "^  enabled: false" "$YAML"; then
    cp "$YAML" "$YAML.bak-$(date +%s)"
    sed -i '' 's/^  enabled: false/  enabled: true/' "$YAML"
    echo "  ✓ companion.yaml hermes_api.enabled false → true"
fi

open -a "Catfish Companion"

echo ""
echo "════════════════════════════════════════════"
echo "✅ 装完 · 首启会自动装 hermes-agent (10-15min)"
echo "════════════════════════════════════════════"
SHEOF
chmod +x "$STAGING/install-mac.sh"
echo "  ✓ install-mac.sh"

cat > "$STAGING/QUICK-START.md" <<'MDEOF'
# Catfish Companion 达华 POC v0.18.0 · 7/19

## 装机 一键
```bash
bash install-mac.sh
```

## 首启后 3 步
1. 等 hermes-agent 装 (10-15min · 只此一次)
2. 面板 → 服务器配置 → 输达华 URL · 保存 · SSO 登录
3. 手机 WeChat 扫码绑 · 发 hi 测

## 7/19 关键 fix
- JWT 自动 refresh (每 25min + 面板改 IP + 启动 · 无感)
- WeChat 中文命令 (`/批准` / `/批准 本次会话` / `/拒绝`) 无条件识别
- hermes 3 处 JWT 同步 (.env + config.yaml + auth.json)
- catfish-auto 动态 model routing

## 出问题 · 3 张截图给 IT
1. 面板服务器配置
2. WeChat Bot 错误
3. `tail -50 ~/catfish-gateway-$(date +%Y%m%d).log`
MDEOF
echo "  ✓ QUICK-START.md"

echo ""
echo "→ [4/4] tar.gz"
cd /tmp
tar -czf "$OUT" "$(basename $STAGING)"
SIZE=$(ls -lh "$OUT" | awk '{print $5}')

echo ""
echo "════════════════════════════════════════════"
echo "✅ 分发包 · $OUT · $SIZE"
echo "════════════════════════════════════════════"
tar -tzf "$OUT" | head -10
echo ""
echo "→ scp 达华现场:"
echo "  scp $OUT dahua@<ip>:/tmp/"
