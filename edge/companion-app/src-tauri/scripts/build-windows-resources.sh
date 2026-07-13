#!/usr/bin/env bash
# Build Windows msi resources for catfish Companion (W1 BL-CATFISH-OFFLINE-INSTALL).
#
# Runs on dev machine (macOS/Linux) or CI. Produces 4 artifacts consumed by
# Tauri wix builder (W2 next task):
#
#   src-tauri/resources/windows/
#     ├── install.ps1                       # patched by patch_install_ps1_offline.py
#     ├── uv.exe                            # Windows x86_64 astral prebuilt
#     ├── cpython-3.11.15-embed.tar.zst     # python-build-standalone Windows x64
#     └── hermes-agent-bundle.tar.gz        # hermes-agent source tarball pinned by
#                                             .hermes-target-version
#
# Pins are top of file. Bump requires re-testing offline install path in Win VM.

set -euo pipefail

# ─── pin ─────────────────────────────────────────────────
UV_VERSION="0.4.30"               # astral-sh/uv release tag (Oct 2024)
PYTHON_VERSION="3.11.15"          # cpython version (must match hermes upstream requires)
PYTHON_BUILD_TAG="20260623"       # python-build-standalone release date tag
                                  # 每 release 只带一个 3.11.x minor 版本;
                                  # 20241016 只到 3.11.10; 3.11.15 需要 20260623+
                                  # 找 URL: https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/<TAG>
                                  # W2.7 (7/12) fix: 原 pin 20241016 错, cpython 3.11.15 不存在于该 release

# ─── GitHub 镜像支持 (国内网络) ──────────────────────
# GH_PROXY 环境变量前缀所有 github.com URL. 国内建议:
#   export GH_PROXY=https://ghfast.top/
#   或 https://mirror.ghproxy.com/  https://gh-proxy.com/
: "${GH_PROXY:=}"

# ─── path 解析 (脚本无论从哪儿跑都定位到 companion-app) ─
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_TAURI_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPANION_APP_DIR="$(cd "${SRC_TAURI_DIR}/.." && pwd)"
CATFISH_ROOT="$(cd "${COMPANION_APP_DIR}/../.." && pwd)"
RESOURCES_DIR="${SRC_TAURI_DIR}/resources/windows"
HERMES_FORK_DIR="${CATFISH_ROOT}/edge/hermes-fork"

HERMES_VERSION="$(cat "${COMPANION_APP_DIR}/.hermes-target-version" | tr -d '[:space:]')"

echo "─── W1 Windows msi resources build ───"
echo "companion-app     : ${COMPANION_APP_DIR}"
echo "resources/windows : ${RESOURCES_DIR}"
echo "hermes target ver : ${HERMES_VERSION}"
echo "uv version        : ${UV_VERSION}"
echo "python version    : ${PYTHON_VERSION} (build ${PYTHON_BUILD_TAG})"
echo ""

mkdir -p "${RESOURCES_DIR}"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# ═════════════════════════════════════════════════════════
# Step 1: patch install.ps1
# ═════════════════════════════════════════════════════════
echo "[1/4] Patching install.ps1 (offline mode) ..."
UPSTREAM_INSTALL_PS1="${HOME}/.hermes/hermes-agent/scripts/install.ps1"
if [[ ! -f "${UPSTREAM_INSTALL_PS1}" ]]; then
    echo "  ERROR: 上游 install.ps1 不存在: ${UPSTREAM_INSTALL_PS1}" >&2
    echo "  先装 hermes: bash <(curl -fsSL https://install.hermes-agent.ai)" >&2
    exit 1
fi
python3 "${HERMES_FORK_DIR}/patch_install_ps1_offline.py" \
    --input "${UPSTREAM_INSTALL_PS1}" \
    --output "${RESOURCES_DIR}/install.ps1"
echo "  ✓ install.ps1 patched → ${RESOURCES_DIR}/install.ps1"
echo ""

# ═════════════════════════════════════════════════════════
# Step 2: uv.exe (Windows x86_64)
# ═════════════════════════════════════════════════════════
echo "[2/4] Downloading uv ${UV_VERSION} (Windows x86_64) ..."
UV_URL="${GH_PROXY}https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
UV_ZIP="${STAGING_DIR}/uv.zip"
UV_EXTRACT_DIR="${STAGING_DIR}/uv-extract"
mkdir -p "${UV_EXTRACT_DIR}"
if [[ -f "${RESOURCES_DIR}/uv.exe" ]]; then
    EXISTING_VER="$(cd /tmp && curl -fsSL "${UV_URL}.sha256" 2>/dev/null || echo "")"
    # 简单起见每次重下 (~15MB, 秒级); dev 心跳时 caching 由 CI 层做
    :
fi
curl -fsSL -o "${UV_ZIP}" "${UV_URL}"
unzip -o -q "${UV_ZIP}" -d "${UV_EXTRACT_DIR}"
UV_EXE="$(find "${UV_EXTRACT_DIR}" -name 'uv.exe' -type f | head -1)"
if [[ -z "${UV_EXE}" ]]; then
    echo "  ERROR: uv.exe 不在 zip 里. archive layout 变了" >&2
    exit 1
fi
cp "${UV_EXE}" "${RESOURCES_DIR}/uv.exe"
UV_SIZE="$(du -h "${RESOURCES_DIR}/uv.exe" | cut -f1)"
echo "  ✓ uv.exe → ${RESOURCES_DIR}/uv.exe (${UV_SIZE})"
echo ""

# ═════════════════════════════════════════════════════════
# Step 3: cpython Windows x64 embed (python-build-standalone)
# ═════════════════════════════════════════════════════════
echo "[3/4] Downloading cpython ${PYTHON_VERSION} (Windows x86_64) ..."
# python-build-standalone URL 结构:
#   cpython-<version>+<date>-x86_64-pc-windows-msvc-install_only.tar.gz
# uv 认识 install_only 布局.
PY_TAG="cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz"
PY_URL="${GH_PROXY}https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/${PY_TAG}"
PY_TAR="${STAGING_DIR}/cpython-src.tar.gz"
PY_EXTRACT="${STAGING_DIR}/cpython-extract"
PY_DEST="${RESOURCES_DIR}/cpython-${PYTHON_VERSION}-embed.zip"
curl -fsSL -o "${PY_TAR}" "${PY_URL}"
mkdir -p "${PY_EXTRACT}"
tar -xzf "${PY_TAR}" -C "${PY_EXTRACT}"
# 重打包为 zip 让 install.ps1 Expand-Archive 直接吃 (W1-4 mitigation:
# python-build-standalone 出的是 tar.gz, 但 install.ps1 用 Expand-Archive
# 只认 zip. 转成 zip 让 offline 分支跟上游同一 API).
(cd "${PY_EXTRACT}" && zip -qr "${PY_DEST}" .)
PY_SIZE="$(du -h "${PY_DEST}" | cut -f1)"
echo "  ✓ cpython → ${PY_DEST} (${PY_SIZE}, tar.gz repacked to zip)"
echo ""

# ═════════════════════════════════════════════════════════
# Step 4: hermes-agent source tarball (pinned to .hermes-target-version)
# ═════════════════════════════════════════════════════════
echo "[4/4] Packing hermes-agent v${HERMES_VERSION} ..."
UPSTREAM_HERMES="${HOME}/.hermes/hermes-agent"
if [[ ! -d "${UPSTREAM_HERMES}" ]]; then
    echo "  ERROR: 上游 hermes-agent 不存在: ${UPSTREAM_HERMES}" >&2
    exit 1
fi
# assert 上游版本对齐 (若开发机上装的 hermes 版本跟 .hermes-target-version 不匹配报错)
CURRENT_VER="$(cat "${UPSTREAM_HERMES}/version.txt" 2>/dev/null || \
               grep -E '^version\s*=' "${UPSTREAM_HERMES}/pyproject.toml" 2>/dev/null | head -1 | \
               sed -E 's/^version\s*=\s*"([^"]+)"/\1/' | tr -d '[:space:]')"
if [[ -z "${CURRENT_VER}" ]]; then
    echo "  WARN: 无法读上游 hermes 版本, 跳过 pin 校验" >&2
elif [[ "${CURRENT_VER}" != "${HERMES_VERSION}" ]]; then
    echo "  ERROR: 上游 hermes 是 ${CURRENT_VER}, 期望 ${HERMES_VERSION}" >&2
    echo "         Bump .hermes-target-version 或 upgrade hermes 使一致" >&2
    exit 1
fi

TAR_DEST="${RESOURCES_DIR}/hermes-agent-bundle.tar.gz"
# 排除大二进制 / 缓存, 只 tar 源码 + scripts + 配置.
# W2.7 (7/12) fix: 原 exclude 漏 venv / venv.bak* / **/__pycache__ 等,
# 打出 1.2GB tar (含 3.3G venv.bak.v0.15.2 + 1.7G venv). 加齐 exclude 后
# 降到 ~200MB (纯源码 + docs + skills).
# W2.9 (7/13) fix: 加 -h dereference symlinks. 根因: hermes-agent/plugins/memory/
#   catfish-memory + catfish-todo-sync 是 dev 机软链指向 catfish repo 里的 plugin
#   源码 (edge/hermes-plugins/*). 不加 -h → Windows tar 展开时 Can't create symlink
#   (Invalid argument). 加 -h → tar 打包时把软链展开成真目录内容, Windows tar 拿
#   到真目录不用创建软链. 副作用: tar 变大 ~10-20MB (2 个 plugin 源码字节), 无痛.
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
echo "─── ✓ Windows msi resources ready ───"
echo "Total: ${TOTAL_SIZE}"
ls -lh "${RESOURCES_DIR}" | grep -v '^total\|.gitignore\|README.md'
echo ""
echo "Next: W2 Tauri wix builder (src-tauri/tauri.conf.json bundle.windows.wix)"
