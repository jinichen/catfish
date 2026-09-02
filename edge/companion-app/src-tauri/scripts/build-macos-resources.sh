#!/usr/bin/env bash
# Build macOS dmg resources for catfish Companion (BL-CATFISH-MAC-OFFLINE-INSTALL 7/15).
#
# 跟 build-windows-resources.sh 平级 (镜像 Windows patch 策略). 跑在 dev macOS 机
# 或 CI. 产出 4 artifacts 消费者是 Tauri bundler (dmg build):
#
#   src-tauri/resources/mac/
#     ├── install.sh                                 # patched by patch_install_sh_offline.py
#     ├── uv                                          # macOS aarch64 astral prebuilt
#     ├── cpython-3.11.15-embed.tar.gz               # python-build-standalone macOS arm64
#     └── hermes-agent-bundle.tar.gz                 # hermes-agent source tarball pinned
#
# Pin 在文件顶部. Bump 后需重跑本脚本, 重 build dmg, 员工机装 verify offline install.

set -euo pipefail

# ─── pin (跟 Windows 版对齐) ─────────────────────────────
PYTHON_VERSION="3.11.15"           # cpython version (must match hermes upstream requires)
PYTHON_BUILD_TAG="20260623"        # python-build-standalone release date tag

# ─── GitHub 镜像支持 (国内网络, 跟 Windows 版同) ──────
: "${GH_PROXY:=}"

# ─── path 解析 ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_TAURI_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPANION_APP_DIR="$(cd "${SRC_TAURI_DIR}/.." && pwd)"
CATFISH_ROOT="$(cd "${COMPANION_APP_DIR}/../.." && pwd)"
RESOURCES_DIR="${SRC_TAURI_DIR}/resources/mac"
HERMES_FORK_DIR="${CATFISH_ROOT}/edge/hermes-fork"
UV_VERSION="$(tr -d '[:space:]' < "${COMPANION_APP_DIR}/.uv-version")"
if [[ -z "${UV_VERSION}" ]]; then
    echo "  ERROR: .uv-version 为空: ${COMPANION_APP_DIR}/.uv-version" >&2
    exit 1
fi

HERMES_VERSION="$(cat "${COMPANION_APP_DIR}/.hermes-target-version" | tr -d '[:space:]')"

echo "─── macOS dmg resources build (BL-CATFISH-MAC-OFFLINE-INSTALL) ───"
echo "companion-app     : ${COMPANION_APP_DIR}"
echo "resources/mac     : ${RESOURCES_DIR}"
echo "hermes target ver : ${HERMES_VERSION}"
echo "uv version        : ${UV_VERSION}"
echo "python version    : ${PYTHON_VERSION} (build ${PYTHON_BUILD_TAG})"
echo ""

mkdir -p "${RESOURCES_DIR}"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# ═════════════════════════════════════════════════════════
# Step 1: patch install.sh
# ═════════════════════════════════════════════════════════
echo "[1/4] Patching install.sh (offline mode) ..."
UPSTREAM_INSTALL_SH="${HOME}/.hermes/hermes-agent/scripts/install.sh"
if [[ ! -f "${UPSTREAM_INSTALL_SH}" ]]; then
    echo "  ERROR: 上游 install.sh 不存在: ${UPSTREAM_INSTALL_SH}" >&2
    echo "  先装 hermes:" >&2
    echo "    git clone --depth 1 --branch v2026.7.1 https://github.com/NousResearch/hermes-agent.git ~/.hermes/hermes-agent" >&2
    exit 1
fi
python3 "${HERMES_FORK_DIR}/patch_install_sh_offline.py" \
    --input "${UPSTREAM_INSTALL_SH}" \
    --output "${RESOURCES_DIR}/install.sh"
echo "  ✓ install.sh patched → ${RESOURCES_DIR}/install.sh"
echo ""

# ═════════════════════════════════════════════════════════
# Step 2: uv (macOS aarch64)
# ═════════════════════════════════════════════════════════
echo "[2/4] Downloading uv ${UV_VERSION} (macOS aarch64) ..."
UV_URL="${GH_PROXY}https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-apple-darwin.tar.gz"
UV_TAR="${STAGING_DIR}/uv.tar.gz"
UV_EXTRACT_DIR="${STAGING_DIR}/uv-extract"
mkdir -p "${UV_EXTRACT_DIR}"
curl -fsSL -o "${UV_TAR}" "${UV_URL}"
tar -xzf "${UV_TAR}" -C "${UV_EXTRACT_DIR}"
UV_BIN="$(find "${UV_EXTRACT_DIR}" -name 'uv' -type f | head -1)"
if [[ -z "${UV_BIN}" ]]; then
    echo "  ERROR: uv binary 不在 tar 里. archive layout 变了" >&2
    exit 1
fi
cp "${UV_BIN}" "${RESOURCES_DIR}/uv"
chmod +x "${RESOURCES_DIR}/uv"
UV_SIZE="$(du -h "${RESOURCES_DIR}/uv" | cut -f1)"
echo "  ✓ uv → ${RESOURCES_DIR}/uv (${UV_SIZE})"
echo ""

# ═════════════════════════════════════════════════════════
# Step 3: cpython macOS aarch64 embed (python-build-standalone)
# ═════════════════════════════════════════════════════════
echo "[3/4] Downloading cpython ${PYTHON_VERSION} (macOS aarch64) ..."
# python-build-standalone URL 结构:
#   cpython-<version>+<date>-aarch64-apple-darwin-install_only.tar.gz
# uv 认识 install_only 布局.
PY_TAG="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PY_URL="${GH_PROXY}https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_TAG}"
PY_DEST="${RESOURCES_DIR}/cpython-${PYTHON_VERSION}-embed.tar.gz"
curl -fsSL -o "${PY_DEST}" "${PY_URL}"
# mac 端 install.sh 直接 tar -xzf 就能吃, 不用 repack (跟 Windows 不同, Windows
# 上 install.ps1 用 Expand-Archive 只认 zip, mac 上 tar 是原生).
PY_SIZE="$(du -h "${PY_DEST}" | cut -f1)"
echo "  ✓ cpython → ${PY_DEST} (${PY_SIZE}, native tar.gz)"
echo ""

# ═════════════════════════════════════════════════════════
# Step 4: hermes-agent source tarball
# ═════════════════════════════════════════════════════════
echo "[4/4] Packing hermes-agent v${HERMES_VERSION} ..."
UPSTREAM_HERMES="${HOME}/.hermes/hermes-agent"
if [[ ! -d "${UPSTREAM_HERMES}" ]]; then
    echo "  ERROR: 上游 hermes-agent 不存在: ${UPSTREAM_HERMES}" >&2
    exit 1
fi
# assert 上游版本对齐 (BSD sed 不支持 \s, 用 awk 抓引号内容更兼容)
CURRENT_VER="$(cat "${UPSTREAM_HERMES}/version.txt" 2>/dev/null || \
               grep '^version' "${UPSTREAM_HERMES}/pyproject.toml" 2>/dev/null | head -1 | \
               awk -F'"' '{print $2}' | tr -d '[:space:]')"
if [[ -z "${CURRENT_VER}" ]]; then
    echo "  WARN: 无法读上游 hermes 版本, 跳过 pin 校验" >&2
elif [[ "${CURRENT_VER}" != "${HERMES_VERSION}" ]]; then
    echo "  ERROR: 上游 hermes 是 ${CURRENT_VER}, 期望 ${HERMES_VERSION}" >&2
    echo "         Bump .hermes-target-version 或 upgrade hermes 使一致" >&2
    exit 1
fi

TAR_DEST="${RESOURCES_DIR}/hermes-agent-bundle.tar.gz"
# 排除大二进制 / 缓存 (跟 Windows 版对齐)
HERMES_BASE="$(basename "${UPSTREAM_HERMES}")"
tar czhf "${TAR_DEST}" \
    -C "$(dirname "${UPSTREAM_HERMES}")" \
    --exclude="${HERMES_BASE}/.git" \
    --exclude="${HERMES_BASE}/__pycache__" \
    --exclude="${HERMES_BASE}/**/__pycache__" \
    --exclude="${HERMES_BASE}/venv" \
    --exclude="${HERMES_BASE}/venv.bak*" \
    --exclude="${HERMES_BASE}/.venv" \
    --exclude="${HERMES_BASE}/**/.venv" \
    --exclude="${HERMES_BASE}/node_modules" \
    --exclude="${HERMES_BASE}/**/node_modules" \
    --exclude="${HERMES_BASE}/target" \
    --exclude="${HERMES_BASE}/**/target" \
    --exclude="${HERMES_BASE}/.pytest_cache" \
    --exclude="${HERMES_BASE}/**/.pytest_cache" \
    --exclude="${HERMES_BASE}/.mypy_cache" \
    --exclude="${HERMES_BASE}/**/.mypy_cache" \
    --exclude="${HERMES_BASE}/.ruff_cache" \
    --exclude="${HERMES_BASE}/dist" \
    --exclude="${HERMES_BASE}/**/dist" \
    --exclude="${HERMES_BASE}/*.pyc" \
    --exclude="${HERMES_BASE}/**/*.pyc" \
    --exclude="${HERMES_BASE}/logs" \
    --exclude="${HERMES_BASE}/*.log" \
    "${HERMES_BASE}"
TAR_SIZE="$(du -h "${TAR_DEST}" | cut -f1)"
echo "  ✓ hermes-agent → ${TAR_DEST} (${TAR_SIZE})"
echo ""

# ═════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════
TOTAL_SIZE="$(du -sh "${RESOURCES_DIR}" | cut -f1)"
echo "─── ✓ macOS dmg resources ready ───"
echo "Total: ${TOTAL_SIZE}"
ls -lh "${RESOURCES_DIR}" | grep -v '^total\|.gitignore\|README.md'
echo ""
echo "Next: tauri.conf.json 加 mac bundle.resources 段 + npm run tauri:build"
