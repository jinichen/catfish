#!/bin/bash
# build-windows-hermes-resources.sh —— Windows MSI 的 hermes 运行时资源 (10/14, hermes v0.22.0 起)
#
# 产出 src-tauri/resources/windows/ 下:
#   uv.exe                          钉 .uv-version
#   cpython-3.14.7-embed.tar.gz     python-build-standalone 20260901 · x86_64-pc-windows-msvc
#   hermes-agent-bundle.tar.gz      源码 + catfish 插件 + wheelhouse/ (win_amd64) + catfish-requirements.txt
#                                   + install-stamp.json (updateMechanism=external)
#   chromium-embed.tar.gz           wheelhouse 里那版 Python playwright 对应的 Chromium (win64)
#
# 员工机上 Companion 自己装 (src-tauri/src/commands/hermes_install_native.rs, mac 同一套),
# 不再跑上游 install.ps1 —— v0.22 上游安装器换成 hermes pm (只认 Python 3.14、联网装),
# 离线补丁 patch_install_ps1_offline.py 的锚点全部对不上。
#
# 在哪跑都行 (macOS 开发机 / Windows CI 的 Git Bash / Linux): 要的 wheel 都按 win_amd64 交叉取,
# Windows 上 hermes 依赖 + 额外包全都有现成 3.14 轮子, 不需要现编。脚本自己下一份**构建机**用的
# Python 3.14 + uv 来跑这些工具, 不依赖构建机装了什么。
#
# 用法: bash scripts/build-windows-hermes-resources.sh
# 邮件 / 微信读取器资源 (catfish-email-dist / catfish-wechat-reader-dist) 不在这里, 见 CI 和 build-msi-local.ps1。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPANION="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO="$(cd "$COMPANION/../.." && pwd)"
RESOURCES="$COMPANION/src-tauri/resources/windows"
WORK="${CATFISH_WIN_BUILD_DIR:-${TMPDIR:-/tmp}/catfish-win-hermes}"
. "$SCRIPT_DIR/lib-download.sh"

PYTHON_VERSION="3.14.7"
PYTHON_BUILD_TAG="20260901"   # 跟 build-mac-resources.sh / 上游 pm/lock.json 同一份
TARGET="x86_64-pc-windows-msvc"
UV_VERSION="$(tr -d '[:space:]' < "$COMPANION/.uv-version")"
HERMES_TAG="$(tr -d '[:space:]' < "$COMPANION/.hermes-git-tag")"
HERMES_COMMIT="$(tr -d '[:space:]' < "$COMPANION/.hermes-git-commit")"

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)   HOST="aarch64-apple-darwin" ;;
    Darwin-x86_64)  HOST="x86_64-apple-darwin" ;;
    Linux-x86_64)   HOST="x86_64-unknown-linux-gnu" ;;
    MINGW*|MSYS*|CYGWIN*) HOST="x86_64-pc-windows-msvc" ;;
    *) echo "❌ 不认识的构建机: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

echo "==============================================="
echo "  Windows hermes 资源 · hermes $HERMES_TAG · Python $PYTHON_VERSION · uv $UV_VERSION"
echo "  构建机: $HOST · 工作目录: $WORK"
echo "==============================================="
rm -rf "$WORK" && mkdir -p "$WORK" "$RESOURCES"
# 上一代资源 (install.ps1 + 3.11 + hermes-deps-dist) 必须删掉 —— 否则 tauri 照 resources 映射打进去
rm -f "$RESOURCES/install.ps1" "$RESOURCES/hermes-deps-dist.tar.gz" \
      "$RESOURCES"/cpython-3.11.*-embed.zip "$RESOURCES"/cpython-3.11.*-embed.tar.*

pbs_url() {  # $1=triple
    echo "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-$1-install_only.tar.gz"
}
uv_url() {   # $1=triple
    case "$1" in
        *windows*) echo "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-$1.zip" ;;
        *)         echo "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-$1.tar.gz" ;;
    esac
}

echo ""
echo "=== [1/6] 构建机用的 Python + uv ($HOST) ==="
download_with_retry "$(pbs_url "$HOST")" "$WORK/host-python.tar.gz"
mkdir -p "$WORK/host" && tar xzf "$WORK/host-python.tar.gz" -C "$WORK/host"
case "$HOST" in
    *windows*) HOST_PY="$WORK/host/python/python.exe" ;;
    *)         HOST_PY="$WORK/host/python/bin/python3" ;;
esac
"$HOST_PY" -V
case "$HOST" in
    *windows*)
        download_with_retry "$(uv_url "$HOST")" "$WORK/host-uv.zip"
        "$HOST_PY" -m zipfile -e "$WORK/host-uv.zip" "$WORK/host-uv"
        HOST_UV="$(find "$WORK/host-uv" -name uv.exe | head -1)" ;;
    *)
        download_with_retry "$(uv_url "$HOST")" "$WORK/host-uv.tar.gz"
        mkdir -p "$WORK/host-uv" && tar xzf "$WORK/host-uv.tar.gz" -C "$WORK/host-uv"
        HOST_UV="$(find "$WORK/host-uv" -name uv -type f | head -1)" ;;
esac
"$HOST_UV" --version

echo ""
echo "=== [2/6] 目标机的 uv.exe + Python ($TARGET) ==="
download_with_retry "$(uv_url "$TARGET")" "$WORK/uv-win.zip"
"$HOST_PY" -m zipfile -e "$WORK/uv-win.zip" "$WORK/uv-win"
cp "$(find "$WORK/uv-win" -name uv.exe | head -1)" "$RESOURCES/uv.exe"
download_with_retry "$(pbs_url "$TARGET")" "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz"
# Windows 上 tar 列表行尾带 \r (Git Bash / bsdtar), 比对前去掉
tar tzf "$RESOURCES/cpython-${PYTHON_VERSION}-embed.tar.gz" | tr -d '\r' | grep -qx "python/python.exe" \
    || { echo "❌ Windows Python 包里没有 python/python.exe (pbs 布局变了?)"; exit 1; }
echo "  OK uv.exe · cpython-${PYTHON_VERSION}-embed.tar.gz"

echo ""
echo "=== [3/6] hermes 源码 $HERMES_TAG + catfish 插件 ==="
HERMES_SRC="$WORK/hermes-agent-src"
. "$SCRIPT_DIR/fetch-hermes-src.sh"
mkdir -p "$HERMES_SRC/plugins/memory"
for plugin in catfish-memory; do
    src="$REPO/edge/hermes-plugins/$plugin"
    dest="$HERMES_SRC/plugins/memory/$plugin"
    mkdir -p "$dest"
    n="$(git -C "$src" ls-files | wc -l | tr -d ' ')"
    [ "$n" -gt 0 ] || { echo "❌ $src 里没有 git 跟踪的文件"; exit 1; }
    (cd "$src" && git ls-files -z | xargs -0 tar cf -) | (cd "$dest" && tar xf -)
    # 10/15: 插件读的两份受控词表在 edge/contracts/, 装好后插件旁边没有它 —— 随包放进插件目录
    mkdir -p "$dest/contracts"
    cp "$REPO/edge/contracts"/wiki_relation_vocab.json "$REPO/edge/contracts"/wiki_type_vocab.json "$dest/contracts/" || exit 1
    echo "  copied $plugin ($n 个文件)"
done

echo ""
echo "=== [4/6] wheelhouse (win_amd64) + 安装戳 ==="
"$HOST_PY" "$SCRIPT_DIR/build_hermes_wheelhouse.py" \
    --src "$HERMES_SRC" --uv "$HOST_UV" --target "$TARGET" --python "$HOST_PY"
"$HOST_PY" -I "$HERMES_SRC/scripts/write_install_stamp.py" --output "$HERMES_SRC/install-stamp.json" \
    --commit "$HERMES_COMMIT" --branch main --base-version "${HERMES_TAG#v}" --distance 0 \
    --source catfish --update-mechanism external

echo ""
echo "=== [5/6] Chromium (win64, 跟 wheelhouse 里的 playwright 同版) ==="
PW_VERSION="$(grep -E '^playwright==' "$HERMES_SRC/catfish-requirements.txt" | head -1 | sed -E 's/^playwright==([^ ]+).*/\1/')"
[ -n "$PW_VERSION" ] || { echo "❌ catfish-requirements.txt 里没有 playwright"; exit 1; }
"$HOST_UV" venv -q "$WORK/pw-venv" --python "$HOST_PY"
case "$HOST" in *windows*) PW_PY="$WORK/pw-venv/Scripts/python.exe" ;; *) PW_PY="$WORK/pw-venv/bin/python" ;; esac
VIRTUAL_ENV="$WORK/pw-venv" "$HOST_UV" pip install -q "playwright==$PW_VERSION"
PW_CACHE="$WORK/ms-playwright"
PLAYWRIGHT_BROWSERS_PATH="$PW_CACHE" PLAYWRIGHT_HOST_PLATFORM_OVERRIDE="win64" \
    "$PW_PY" -m playwright install chromium
CHROMIUM_DIRS=()
while IFS= read -r d; do CHROMIUM_DIRS+=("$d"); done < <(cd "$PW_CACHE" && ls -1d chromium-* chromium_headless_shell-* 2>/dev/null | sort)
[ "${#CHROMIUM_DIRS[@]}" -gt 0 ] || { echo "❌ 没下到 Chromium"; exit 1; }
find "$PW_CACHE" -path "*chrome-win64/chrome.exe" | grep -q . \
    || { echo "❌ 下到的不是 Windows 版 Chromium (没有 chrome-win64/chrome.exe)"; exit 1; }
tar czf "$RESOURCES/chromium-embed.tar.gz" -C "$PW_CACHE" "${CHROMIUM_DIRS[@]}"
echo "  OK chromium-embed.tar.gz · playwright $PW_VERSION · ${CHROMIUM_DIRS[*]}"

echo ""
echo "=== [6/6] 打 hermes-agent-bundle.tar.gz ==="
BUNDLE="$RESOURCES/hermes-agent-bundle.tar.gz"
tar czhf "$BUNDLE" -C "$WORK" \
    --exclude='hermes-agent-src/.git' \
    --exclude='hermes-agent-src/venv' \
    --exclude='hermes-agent-src/.venv' \
    --exclude='hermes-agent-src/target' \
    --exclude='hermes-agent-src/apps/desktop' \
    --exclude='hermes-agent-src/website' \
    --exclude='hermes-agent-src/tests' \
    --exclude='hermes-agent-src/tests-js' \
    --exclude='hermes-agent-src/.github' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    hermes-agent-src
"$HOST_PY" "$REPO/edge/hermes-fork/verify_hermes_bundle.py" --input "$BUNDLE"
LIST="$WORK/bundle-list.txt"
tar tzf "$BUNDLE" | tr -d '\r' > "$LIST"
for must in install-stamp.json catfish-requirements.txt pyproject.toml uv.lock pm/lock.json \
            plugins/memory/catfish-memory/__init__.py; do
    grep -qx "hermes-agent-src/$must" "$LIST" || { echo "❌ 归档里缺 $must"; exit 1; }
done
for leak in '\.git/' 'venv/' 'node_modules/'; do
    if grep -qE "^hermes-agent-src/${leak}|/node_modules/" "$LIST"; then
        echo "❌ 归档里混进了 $leak"; exit 1
    fi
done
echo "  OK $(du -h "$BUNDLE" | cut -f1) · $(wc -l < "$LIST" | tr -d ' ') 条 · wheel $(grep -c '^hermes-agent-src/wheelhouse/.*\.whl$' "$LIST") 个"

echo ""
echo "DONE · $RESOURCES"
ls -lh "$RESOURCES"
rm -rf "$WORK"
