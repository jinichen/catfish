#!/bin/bash
# build-mac-resources.sh · BL-MAC-INSTALL-NODE+CHROMIUM-BUNDLE (7/17)
#
# 一键打全 mac dmg 用的 resources/mac/ 6 artifacts:
#   1. install.sh            (patch offline mode · 7 处 marker)
#   2. uv                    (下 astral.sh · macOS arm64 or x64)
#   3. cpython-3.11.15-embed.tar.gz  (下 python-build-standalone · macOS arm64 or x64)
#   4. hermes-agent-bundle.tar.gz    (clone + npm ci + tar 打包)
#   5. node-embed.tar.gz     (下 nodejs.org · v22 darwin arm64 or x64) · 新增
#   6. chromium-embed.tar.gz (npx playwright install chromium + tar 打包) · 新增
#
# 用法:
#   # 打 Apple Silicon (aarch64) 资源:
#   bash scripts/build-mac-resources.sh aarch64
#
#   # 打 Intel (x64) 资源 (在 Apple Silicon mac 上用 rosetta 2):
#   bash scripts/build-mac-resources.sh x64
#
# 完了后跑:
#   npm run tauri build              # aarch64 dmg
#   npm run tauri build -- --target x86_64-apple-darwin  # x64 dmg

set -euo pipefail

ARCH="${1:-aarch64}"
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "x64" ]; then
    echo "❌ 用法: $0 aarch64|x64"
    exit 1
fi

# arch mapping
if [ "$ARCH" = "aarch64" ]; then
    NODE_ARCH="arm64"
    UV_ARCH="aarch64-apple-darwin"
    PY_ARCH="aarch64-apple-darwin"
else
    NODE_ARCH="x64"
    UV_ARCH="x86_64-apple-darwin"
    PY_ARCH="x86_64-apple-darwin"
fi

echo "==============================================="
echo "  build-mac-resources.sh · arch=$ARCH"
echo "  Node arch=$NODE_ARCH · uv=$UV_ARCH · py=$PY_ARCH"
echo "==============================================="

# ─── 下载 helper · retry + gzip verify + ghfast.top 代理 ─────
# 镜像 build-intel-dmg.sh 里的 download_with_retry (7/16), 修 uv 下载 truncated 挂.
# 国内下 GitHub Release 慢/易断 · ghfast.top 加速. 国外可 GH_PROXY="" 直连.
GH_PROXY="${GH_PROXY:-https://ghfast.top/}"

download_with_retry() {
    local url="$1"
    local output="$2"
    local url_proxied="${GH_PROXY}${url}"

    # 每次删旧 · 避免坏 cache 复用
    rm -f "$output"

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        echo "  [attempt $attempt/10] $output"
        # -C - 断点续传, --retry 3 内部小 retry, --retry-max-time 15 min
        if curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 --continue-at - \
                -o "$output" "$url_proxied"; then
            # verify gzip 完整性 (若 tar.gz)
            if [[ "$output" == *.gz ]] || [[ "$output" == *.tar.gz ]]; then
                if gzip -t "$output" 2>/dev/null; then
                    echo "  ✓ 下载完整 (gzip verify OK · $(ls -lh "$output" | awk '{print $5}'))"
                    return 0
                else
                    echo "  ✗ tar 坏 · 删了重下"
                    rm -f "$output"
                fi
            else
                echo "  ✓ 下载完整 ($(ls -lh "$output" | awk '{print $5}'))"
                return 0
            fi
        fi
        # 第 5 次后换直连试试
        if [ $attempt -eq 5 ] && [ -n "$GH_PROXY" ]; then
            echo "  switch to direct (no proxy)"
            url_proxied="$url"
        fi
        sleep 10
    done
    echo "!!! 下 10 次都挂: $url"
    return 1
}

# 项目根
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPANION="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOURCES="$COMPANION/src-tauri/resources/mac"
HERMES_TAG=$(cat "$COMPANION/.hermes-git-tag" | tr -d '[:space:]')

mkdir -p "$RESOURCES"
cd "$COMPANION"

# ─── 1. patch install.sh · offline mode ─────────────────

HERMES_SRC="/tmp/hermes-agent-src-$ARCH"

echo ""
echo "=== [1/6] Clone hermes-agent tag $HERMES_TAG · copy catfish plugins ==="
if [ -d "$HERMES_SRC" ]; then
    echo "  clean $HERMES_SRC"
    rm -rf "$HERMES_SRC"
fi
git clone --depth 1 --branch "$HERMES_TAG" \
    https://github.com/NousResearch/hermes-agent.git "$HERMES_SRC"

# copy catfish plugins
mkdir -p "$HERMES_SRC/plugins/memory"
for plugin in catfish-memory catfish-todo-sync; do
    src="../hermes-plugins/$plugin"
    if [ -d "$src" ]; then
        cp -R "$src" "$HERMES_SRC/plugins/memory/$plugin"
        echo "  copied $plugin"
    fi
done

echo ""
echo "=== [2/6] Patch install.sh offline mode ==="
python3 ../hermes-fork/patch_install_sh_offline.py \
    --input "$HERMES_SRC/scripts/install.sh" \
    --output "$RESOURCES/install.sh"
chmod +x "$RESOURCES/install.sh"

# ─── 3. Node.js darwin binary ────────────────────────────

NODE_VERSION="22.14.0"
NODE_FNAME="node-v${NODE_VERSION}-darwin-${NODE_ARCH}.tar.gz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_FNAME}"

echo ""
echo "=== [3/6] Download Node.js $NODE_VERSION darwin-$NODE_ARCH ==="
# Node.js 官方源 nodejs.org 不走 GH proxy (nodejs.org 直接 CDN 快)
NODE_TMP="/tmp/$NODE_FNAME"
if [ ! -f "$NODE_TMP" ] || ! gzip -t "$NODE_TMP" 2>/dev/null; then
    rm -f "$NODE_TMP"
    curl -fL --retry 3 --retry-delay 5 --retry-max-time 900 -o "$NODE_TMP" "$NODE_URL"
    gzip -t "$NODE_TMP" || { echo "❌ Node tar corrupted, re-run"; exit 1; }
fi
cp "$NODE_TMP" "$RESOURCES/node-embed.tar.gz"
echo "  OK $RESOURCES/node-embed.tar.gz ($(ls -lh "$RESOURCES/node-embed.tar.gz" | awk '{print $5}'))"

# ─── 4. uv binary ───────────────────────────────────────

UV_VERSION="0.4.30"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_ARCH}.tar.gz"

echo ""
echo "=== [4/6] Download uv $UV_VERSION ($UV_ARCH) ==="
UV_TMP="/tmp/uv-$ARCH.tar.gz"
# 走 ghfast.top 代理 · 修 truncated 挂根因
download_with_retry "$UV_URL" "$UV_TMP" || exit 1
UV_EXTRACT="/tmp/uv-extract-$ARCH"
rm -rf "$UV_EXTRACT" && mkdir -p "$UV_EXTRACT"
tar -xzf "$UV_TMP" -C "$UV_EXTRACT"
cp "$UV_EXTRACT/uv-$UV_ARCH/uv" "$RESOURCES/uv"
chmod +x "$RESOURCES/uv"
echo "  OK $RESOURCES/uv ($(ls -lh "$RESOURCES/uv" | awk '{print $5}'))"

# ─── 5. cpython 3.11.15 ─────────────────────────────────

PYTHON_VERSION="3.11.15"
PYTHON_BUILD_TAG="20260623"
PY_FNAME="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${PY_ARCH}-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_FNAME}"

echo ""
echo "=== [5/6] Download cpython $PYTHON_VERSION ($PY_ARCH) ==="
PY_TMP="/tmp/cpython-$ARCH.tar.gz"
# 走 ghfast.top 代理
download_with_retry "$PY_URL" "$PY_TMP" || exit 1
cp "$PY_TMP" "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz"
echo "  OK cpython-embed.tar.gz ($(ls -lh "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz" | awk '{print $5}'))"

# ─── 6. hermes-agent bundle · npm ci · npx playwright install chromium · tar 打包 ─────

echo ""
echo "=== [6a/6] npm ci in hermes-agent (装 node_modules) ==="
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1  # 分开跑 chromium
# 顶级 npm ci
cd "$HERMES_SRC"
if [ -f "package-lock.json" ]; then
    npm ci --no-audit --no-fund --loglevel=error
else
    npm install --no-audit --no-fund --loglevel=error
fi
echo "  OK 顶级 node_modules 装完 ($(du -sh node_modules | cut -f1))"

# BL-HERMES-BUNDLE-SURGICAL (7/17): 手术级瘦身. hermes-agent 顶级 npm ci 装 1GB · 里面
# TOP: node_modules/hermes 811MB (React Native Hermes JS engine · Electron 用) + mermaid
# 80MB + @tabler 77MB + lucide-react 36MB + typescript 23MB + electron-winstaller 30MB.
# 员工机跑 hermes-agent (Python 主) 只需 agent-browser + node-pty + playwright. 删大而不用的.
echo ""
echo "=== [6a.5/6] 手术级瘦身 · 删 npm 大依赖 (Electron/前端/dev 用) ==="
cd "$HERMES_SRC/node_modules" 2>/dev/null || true
SURGICAL_TARGETS=(
    hermes                    # 811 MB · React Native Hermes JS engine
    mermaid @mermaid-js       # 80 MB · 画图库
    @tabler @icons-pack       # 77+27 MB · icon set
    lucide-react              # 36 MB · icon set
    three three-stdlib        # 30 MB · 3D
    electron-winstaller       # 30 MB · electron 装机器
    typescript                # 23 MB · dev tool
    @rolldown                 # 17 MB · bundler
    @tauri-apps               # 15 MB · Tauri (Companion 已含, 重复)
    react-native-*            # React Native (Electron/mobile 用)
    "@types"                  # dev TypeScript types
)
for t in "${SURGICAL_TARGETS[@]}"; do
    # 支持通配符 (react-native-* 之类)
    for match in $t; do
        if [ -e "$match" ]; then
            size=$(du -sh "$match" 2>/dev/null | cut -f1)
            rm -rf "$match"
            echo "  🔪 删 $match ($size)"
        fi
    done
done
cd "$HERMES_SRC"
echo "  OK 手术后 node_modules 大小: $(du -sh node_modules | cut -f1)"

echo ""
echo "=== [6b/6] npx playwright install chromium (arch=$NODE_ARCH) ==="
unset PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD  # 允许下载 chromium
# arch -x86_64 是给 Apple Silicon mac 上跑 Intel 命令用. 若 build aarch64 · 直接跑.
if [ "$ARCH" = "x64" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "  Apple Silicon 上打 Intel · 用 arch -x86_64 npx"
    arch -x86_64 npx --yes playwright install chromium
else
    npx --yes playwright install chromium
fi

echo ""
echo "=== [6c/6] tar pack chromium-embed · from ~/Library/Caches/ms-playwright/ ==="
PW_CACHE="$HOME/Library/Caches/ms-playwright"
if [ ! -d "$PW_CACHE" ]; then
    echo "❌ Playwright cache 目录不存在: $PW_CACHE"
    exit 1
fi
CHROMIUM_TAR="$RESOURCES/chromium-embed.tar.gz"
cd "$PW_CACHE"
# BL-CHROMIUM-LATEST-ONLY (7/17 修 build-mac-resources dmg 挂根因):
# 老 script tar chromium* 会打**所有历史版本** (1200/1208/1217/1228 · 4 版 950MB),
# 应该只打**最新版本** (跟刚 npx playwright install 装的一致).
# sort -V 版本号排序 · tail -1 挑最大.
LATEST_CHROMIUM=$(ls -1d chromium-* 2>/dev/null | grep -v headless_shell | sort -V | tail -1)
LATEST_HEADLESS=$(ls -1d chromium_headless_shell-* 2>/dev/null | sort -V | tail -1)
if [ -z "$LATEST_CHROMIUM" ]; then
    echo "❌ 无 chromium-* 目录 in $PW_CACHE"
    exit 1
fi
echo "  只打最新版本 (省 700MB):"
echo "    chromium:        $LATEST_CHROMIUM"
echo "    headless_shell:  ${LATEST_HEADLESS:-无 · skip}"
# 老版本目录若在 · 提示 (但不主动删 · 别 npm cache 突然缺 chromium 挂)
OLD_COUNT=$(ls -1d chromium-* 2>/dev/null | grep -v headless_shell | wc -l | tr -d ' ')
if [ "$OLD_COUNT" -gt 1 ]; then
    echo "  ⚠ 检到 $OLD_COUNT 个 chromium-* 老版本 · 可手动清理:"
    echo "    ls -1d chromium-* | grep -v headless_shell | sort -V | head -n -1 | xargs rm -rf"
fi
if [ -n "$LATEST_HEADLESS" ]; then
    tar czf "$CHROMIUM_TAR" "$LATEST_CHROMIUM" "$LATEST_HEADLESS"
else
    tar czf "$CHROMIUM_TAR" "$LATEST_CHROMIUM"
fi
echo "  OK $CHROMIUM_TAR ($(ls -lh "$CHROMIUM_TAR" | awk '{print $5}'))"

echo ""
echo "=== [6d/6] tar pack hermes-agent bundle (含 node_modules · 排废件) ==="
# BL-HERMES-BUNDLE-SLIM (7/17): hermes-agent 里 apps/desktop 是 Electron 桌面 app
# (200MB Electron framework + electron/playwright 二次装 1.4GB, 总 1.6GB), 我们
# 用 Tauri Companion 替代它, 完全不用. exclude 后 tar 从 955MB → ~200MB.
# --exclude patterns 支持 shell glob, 无需 leading 路径.
cd /tmp
HERMES_TAR="$RESOURCES/hermes-agent-bundle.tar.gz"
tar czhf "$HERMES_TAR" \
    --exclude="hermes-agent-src-$ARCH/.git" \
    --exclude="hermes-agent-src-$ARCH/venv" \
    --exclude="hermes-agent-src-$ARCH/venv.bak" \
    --exclude="hermes-agent-src-$ARCH/.venv" \
    --exclude="hermes-agent-src-$ARCH/target" \
    --exclude="hermes-agent-src-$ARCH/apps/desktop" \
    -s "|hermes-agent-src-$ARCH|hermes-agent-src|" \
    "hermes-agent-src-$ARCH"
echo "  OK $HERMES_TAR ($(ls -lh "$HERMES_TAR" | awk '{print $5}'))"

# ─── DONE ──────────────────────────────────────────────

echo ""
echo "==============================================="
echo "  DONE · $RESOURCES · 6 artifacts:"
echo "==============================================="
ls -lh "$RESOURCES/"

TOTAL_MB=$(du -sm "$RESOURCES" | cut -f1)
echo ""
echo "总大小: ${TOTAL_MB} MB"
echo ""
echo "下一步 (build dmg):"
if [ "$ARCH" = "aarch64" ]; then
    echo "  cd $COMPANION && npm run tauri build"
else
    echo "  cd $COMPANION && npm run tauri build -- --target x86_64-apple-darwin"
fi
