#!/usr/bin/env python3
"""Catfish install.sh offline patch tool (mac 版, 7/15 BL-CATFISH-MAC-OFFLINE-INSTALL).

# 为啥独立脚本, 不塞 apply_brand_patch.py

跟 patch_install_ps1_offline.py 同思路 — install.sh patch 场景跟品牌 patch
完全不同:
- 目标是 **dmg build artifact** (打包时 codegen, 一次性写死)
- 员工机跑的是 **dmg 里预 patch 好的 install.sh**, 跟上游没 git 关系
- 品牌 patch 的 "git hook 自动重打" 机制在员工机上根本无用

**结论**: 语义分开 → 独立脚本 → 独立生命周期 → 独立测试.

# 4 处 offline patch (跟 ps1 版对齐, 长期一致维护)

1. `case $1 in ... -h|--help)` 前 (line ~157): 加 `--offline-source-dir` /
   `--offline-uv` / `--offline-python-tar` 3 个 case 分支. 参数为空 = 走上游
   canonical 网络路径, **零副作用**.

2. `install_uv()` (line ~547): 在 `log_info "Installing managed uv into
   $HERMES_HOME/bin ..."` 之前加 offline copy 分支 — `$OFFLINE_UV` 非空且文件存
   → cp 到 `$HERMES_HOME/bin/uv` → 早 return, 跳过 astral.sh 网络下载.

3. `check_python()` (line ~610): 在 `log_info "Python $PYTHON_VERSION not found,
   installing via uv..."` 之前加 offline 分支 — `$OFFLINE_PYTHON_TAR` 非空且文件
   存 → tar 解压到 `$UV_PYTHON_INSTALL_DIR` → `uv python find` verify → 早 return.

4. clone repo (line ~1270 `else` 段): 在 `# Try SSH first ...` 之前加 offline
   分支 — `$OFFLINE_SOURCE_DIR` 非空且目录存 → cp -R 到 `$INSTALL_DIR` → git init
   → 设 `_catfish_offline_done=true` 让下面 SSH/HTTPS clone 段整个跳过.

# SHA256 pin

hardcode 当前上游 install.sh SHA256. drift → exit 1 报错, 强制 audit patch.
Bump 步骤:
1. `shasum -a 256 ~/.hermes/hermes-agent/scripts/install.sh` 拿新 SHA
2. 手动验证 4 处 anchor 是否仍稳定 (bash 上下文没变)
3. 更新脚本顶部 UPSTREAM_SHA256

# 用法

    python3 patch_install_sh_offline.py \\
        --input ~/.hermes/hermes-agent/scripts/install.sh \\
        --output ../companion-app/src-tauri/resources/mac/install.sh

    python3 patch_install_sh_offline.py --check --input .../install.sh
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


# ─── 上游 pin ──────────────────────────────────────────────

#: 当前测试通过的 install.sh SHA256. Bump 时必须重新 audit 4 处 anchor 是否稳定.
#: 计算: `shasum -a 256 ~/.hermes/hermes-agent/scripts/install.sh`
UPSTREAM_SHA256 = "a93c65b01ea392e179cf872e182bd01a2b65c0c15f17833e9f9569033ef10e07"

#: 上游预期行数 (rough sanity check, 不 fatal, 只 warn)
UPSTREAM_LINES_EXPECTED = 3133

#: Patch marker — script 重跑幂等靠这个
MARKER = "# CATFISH-OFFLINE-PATCH-v1"


# ─── 4 处 bash 代码块 (patch payload) ─────────────────


PATCH_1_PARAM = f"""        --offline-source-dir)
            # {MARKER}: hermes-agent 预解压目录 (跳 git clone)
            OFFLINE_SOURCE_DIR="$2"
            shift 2
            ;;
        --offline-uv)
            # {MARKER}: 预下载 uv binary 路径 (跳 astral.sh)
            OFFLINE_UV="$2"
            shift 2
            ;;
        --offline-python-tar)
            # {MARKER}: 预下载 python tar.gz 路径 (跳 uv python install)
            OFFLINE_PYTHON_TAR="$2"
            shift 2
            ;;
        --offline-node-tar)
            # {MARKER}: 预下载 Node.js tar.gz 路径 (BL-MAC-INSTALL-NODE-BUNDLE 7/17)
            # 跳过在线下 nodejs.org · 员工机无公网必挂
            OFFLINE_NODE_TAR="$2"
            shift 2
            ;;
        --offline-chromium-tar)
            # {MARKER}: 预下载 Playwright chromium tar.gz 路径 (BL-MAC-INSTALL-CHROMIUM-BUNDLE 7/17)
            # 解压到 ~/Library/Caches/ms-playwright/ 让 Playwright 自动 detect
            OFFLINE_CHROMIUM_TAR="$2"
            shift 2
            ;;
        -h|--help)"""


PATCH_2_INSTALL_UV = f"""    {MARKER}: Catfish offline — copy embedded uv, skip astral.sh
    if [ -n "${{OFFLINE_UV:-}}" ] && [ -f "$OFFLINE_UV" ]; then
        log_info "Catfish offline: copying uv from $OFFLINE_UV"
        mkdir -p "$HERMES_HOME/bin"
        cp -f "$OFFLINE_UV" "$_managed_uv"
        chmod +x "$_managed_uv"
        UV_CMD="$_managed_uv"
        UV_VERSION=$("$UV_CMD" --version 2>/dev/null)
        log_success "Managed uv installed from offline bundle ($UV_VERSION)"
        return 0
    fi

    log_info "Installing managed uv into $HERMES_HOME/bin ..."
    mkdir -p "$HERMES_HOME/bin"
"""


# uv python cache 路径: 走 UV_PYTHON_INSTALL_DIR 环境变量 (uv 0.4+ 官方支持).
# uv doc: https://docs.astral.sh/uv/reference/environment/#uv_python_install_dir
# 让 uv 自己决定 cache root, 我们只 push tar 内容进去.
PATCH_3_CHECK_PYTHON = f"""    {MARKER}: Catfish offline — expand embedded python tar
    if [ -n "${{OFFLINE_PYTHON_TAR:-}}" ] && [ -f "$OFFLINE_PYTHON_TAR" ]; then
        log_info "Catfish offline: expanding python from $OFFLINE_PYTHON_TAR"
        _uv_py_root="${{UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}}"
        mkdir -p "$_uv_py_root"
        if tar -xzf "$OFFLINE_PYTHON_TAR" -C "$_uv_py_root" 2>/dev/null; then
            if PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null)"; then
                PYTHON_FOUND_VERSION="$("$PYTHON_PATH" --version 2>/dev/null)"
                log_success "Python installed from offline bundle: $PYTHON_FOUND_VERSION"
                return 0
            fi
        fi
        log_warn "Catfish offline: python tar failed — 回退网络路径"
    fi

    # Python not found — use uv to install it (no sudo needed!)
    log_info "Python $PYTHON_VERSION not found, installing via uv..."
"""


PATCH_4_INSTALL_REPO = f"""    else
        {MARKER}: Catfish offline — copy pre-extracted hermes-agent
        _catfish_offline_done=false
        if [ -n "${{OFFLINE_SOURCE_DIR:-}}" ] && [ -d "$OFFLINE_SOURCE_DIR" ]; then
            log_info "Catfish offline: copying hermes-agent from $OFFLINE_SOURCE_DIR"
            mkdir -p "$(dirname "$INSTALL_DIR")"
            # 若 INSTALL_DIR 已存在, 备份 (rename with timestamp)
            if [ -e "$INSTALL_DIR" ]; then
                _backup="$INSTALL_DIR.replaced-$(date +%Y%m%d-%H%M%S)"
                mv "$INSTALL_DIR" "$_backup" 2>/dev/null || true
            fi
            # cp -R 保留 permission + symlink
            cp -R "$OFFLINE_SOURCE_DIR" "$INSTALL_DIR"
            # git init 让 hermes update 未来有网时能 pull
            (cd "$INSTALL_DIR" && \\
                git init 2>/dev/null || true; \\
                git config core.autocrlf false 2>/dev/null || true; \\
                git remote add origin "$REPO_URL_HTTPS" 2>/dev/null || true)
            log_success "hermes-agent installed from offline bundle"
            _catfish_offline_done=true
        fi

        if [ "$_catfish_offline_done" = "false" ]; then
        # Try SSH first (for private repo access), fall back to HTTPS"""


PATCH_5_INSTALL_REPO_CLOSE = f"""            fi
        fi
        fi
    fi

    cd "$INSTALL_DIR\""""


# 处 6 · install_node (line ~830): 员工机无 Node · install_node 会下 nodejs.org tar 装.
# 员工无公网必挂 · BL-MAC-INSTALL-NODE-BUNDLE (7/17): 若 --offline-node-tar 传, 解压到
# ~/.hermes/node/ · export PATH · HAS_NODE=true, return 0.
PATCH_6_INSTALL_NODE = f"""install_node() {{
    {MARKER}: Catfish offline — 解压内嵌 Node.js tar (员工无公网必挂 nodejs.org 下载)
    if [ -n "${{OFFLINE_NODE_TAR:-}}" ] && [ -f "$OFFLINE_NODE_TAR" ]; then
        log_info "Catfish offline: 解压 Node.js from $OFFLINE_NODE_TAR"
        mkdir -p "$HERMES_HOME/node"
        # tar 里通常是 node-v22.x.x-darwin-arm64/ 顶级目录, 我们 --strip-components=1 直接解到 $HERMES_HOME/node/
        if tar -xzf "$OFFLINE_NODE_TAR" -C "$HERMES_HOME/node" --strip-components=1 2>/dev/null; then
            export PATH="$HERMES_HOME/node/bin:$PATH"
            if command -v node &> /dev/null; then
                local installed_ver
                installed_ver=$(node --version 2>/dev/null)
                log_success "Node.js $installed_ver installed from Catfish offline bundle"
                HAS_NODE=true
                return 0
            else
                log_warn "Catfish offline: Node.js 解压后仍 command -v node 挂"
            fi
        else
            log_warn "Catfish offline: Node.js tar 解压挂 - fallback nodejs.org (员工无公网必挂)"
        fi
    fi

    if [ "$DISTRO" = "termux" ]; then"""


# 处 7 · install_node_deps (line ~2114): offline mode 短路整个 npm install + Playwright chromium 装.
# BL-MAC-INSTALL-NPM-OFFLINE + CHROMIUM-BUNDLE (7/17):
#   - node_modules 已在 dmg (build 时 npm ci 打的) · skip npm install
#   - 若 --offline-chromium-tar 传 · tar 解压到 ~/Library/Caches/ms-playwright/ · Playwright auto-detect
#   - 完全跳过原代码里的 npm install + case DISTRO ... npx playwright install ... esac
# 单一 anchor 短路 · 无嵌套括号闭合地雷.
PATCH_7_NPM_AND_CHROMIUM = f"""install_node_deps() {{
    {MARKER}: Catfish offline — 短路整个 npm + Playwright chromium 装 (dmg 已打 node_modules + chromium)
    if [ -n "${{OFFLINE_SOURCE_DIR:-}}" ] || [ -n "${{OFFLINE_SOURCE_TAR:-}}" ]; then
        if [ "$HAS_NODE" = false ]; then
            log_info "Catfish offline: Node 未装 (unlikely with offline Node bundle), skip Node deps"
            return 0
        fi

        # A. npm install skip if node_modules 已在
        if [ -d "$INSTALL_DIR/node_modules" ]; then
            log_info "Catfish offline: node_modules already present, skip npm install"
            log_success "Node.js dependencies already installed (Catfish offline bundle)"
        else
            log_warn "Catfish offline: node_modules 缺 · fallback 原 npm install (需公网)"
        fi

        # B. Playwright chromium 解压 to ~/Library/Caches/ms-playwright/
        if [ -n "${{OFFLINE_CHROMIUM_TAR:-}}" ] && [ -f "$OFFLINE_CHROMIUM_TAR" ]; then
            _chromium_dest="$HOME/Library/Caches/ms-playwright"
            log_info "Catfish offline: 解压 Playwright Chromium bundle 到 $_chromium_dest"
            mkdir -p "$_chromium_dest"
            if tar -xzf "$OFFLINE_CHROMIUM_TAR" -C "$_chromium_dest" 2>/dev/null; then
                log_success "Playwright Chromium installed from Catfish offline bundle"
            else
                log_warn "Catfish offline: chromium tar 解压挂 (browser_* tools 首次用时会挂)"
            fi
        else
            log_info "Catfish offline: 无 -offline-chromium-tar, skip Playwright Chromium 装 (browser_* tools 用不了)"
        fi

        return 0
    fi

    if [ "$HAS_NODE" = false ]; then
        log_info "Skipping Node.js dependencies (Node not installed)"
        return 0
    fi
"""


# ─── 5 处 anchor (完全精确的 unique string) ────────────────


ANCHORS = {
    "param_help": (
        # BEFORE: -h|--help) case 行
        "        -h|--help)",
        # AFTER: 前面加 5 个 --offline-* case (3 原 + 2 新 · node + chromium)
        PATCH_1_PARAM,
    ),
    "install_uv": (
        # BEFORE: install_uv() 里两行
        '    log_info "Installing managed uv into $HERMES_HOME/bin ..."\n'
        '    mkdir -p "$HERMES_HOME/bin"\n',
        # AFTER: 前面加 offline check
        PATCH_2_INSTALL_UV,
    ),
    "check_python": (
        # BEFORE: check_python() 里两行 (Python not found 触发 uv install)
        '    # Python not found — use uv to install it (no sudo needed!)\n'
        '    log_info "Python $PYTHON_VERSION not found, installing via uv..."\n',
        # AFTER: 前面加 offline check
        PATCH_3_CHECK_PYTHON,
    ),
    "install_repo_open": (
        # BEFORE: clone repo else 块开头 + SSH clone 段
        "    else\n"
        "        # Try SSH first (for private repo access), fall back to HTTPS",
        # AFTER: 加 offline check + wrap 原 clone 段
        PATCH_4_INSTALL_REPO,
    ),
    "install_repo_close": (
        # BEFORE: clone else 块末尾 fi + cd
        "            fi\n"
        "        fi\n"
        "    fi\n"
        "\n"
        '    cd "$INSTALL_DIR"',
        # AFTER: 多加一个 fi (关闭 offline if)
        PATCH_5_INSTALL_REPO_CLOSE,
    ),
    "install_node": (
        # BEFORE: install_node 函数开头 + termux 检查
        'install_node() {\n'
        '    if [ "$DISTRO" = "termux" ]; then',
        # AFTER: 前面加 offline Node tar 解压
        PATCH_6_INSTALL_NODE,
    ),
    "install_node_deps": (
        # BEFORE: install_node_deps 函数开头 + HAS_NODE=false skip
        'install_node_deps() {\n'
        '    if [ "$HAS_NODE" = false ]; then\n'
        '        log_info "Skipping Node.js dependencies (Node not installed)"\n'
        '        return 0\n'
        '    fi\n',
        # AFTER: 前面加 offline mode 短路整个 (npm + chromium)
        PATCH_7_NPM_AND_CHROMIUM,
    ),
}


# ─── 核心 patch 函数 ──────────────────────────────────────


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_already_patched(text: str) -> bool:
    """是否已 patched (幂等). marker 命中即认为已 patch."""
    return MARKER in text


def check_upstream_sha256(text: str, *, strict: bool = True) -> str:
    """校验上游 install.sh 是否是我们 audit 过的版本.

    Returns actual SHA256. Raises SystemExit(1) if strict and mismatch.
    """
    actual = sha256_of(text)
    if actual == UPSTREAM_SHA256:
        return actual
    msg = (
        f"[CATFISH-OFFLINE-PATCH] SHA256 drift!\n"
        f"  expected: {UPSTREAM_SHA256}\n"
        f"  actual:   {actual}\n"
        f"上游 install.sh 变了 (可能新参数 / anchor 挪位置). 必须:\n"
        f"  1. 重新 audit 5 处 anchor 是否稳定\n"
        f"  2. 更新脚本顶部 UPSTREAM_SHA256\n"
        f"  3. verify 3 处 offline bash 分支跟上游 flow 兼容\n"
    )
    if strict:
        print(msg, file=sys.stderr)
        raise SystemExit(1)
    print(f"[WARN] {msg}", file=sys.stderr)
    return actual


def apply_patches(text: str) -> str:
    """5 处 anchor 逐一替换, 每处 must 命中 1 次 (不多不少)."""
    result = text
    for name, (before, after) in ANCHORS.items():
        count = result.count(before)
        if count == 0:
            print(
                f"[ERROR] anchor {name!r} 找不到 (0 命中). 上游可能改了此段代码.",
                file=sys.stderr,
            )
            # dump 一小段调试用
            print(f"  BEFORE (前 100 字符): {before[:100]!r}", file=sys.stderr)
            raise SystemExit(2)
        if count > 1:
            print(
                f"[ERROR] anchor {name!r} 匹配 {count} 处 (期望 1). "
                f"上游可能重构了, anchor 不再 unique. 需重新 audit.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        result = result.replace(before, after, 1)
    return result


def verify_patched(patched_text: str) -> None:
    """patched 输出 sanity check — 5 个 --offline-* 参数 + 7 处 marker 都在."""
    required_symbols = [
        "OFFLINE_SOURCE_DIR",
        "OFFLINE_UV",
        "OFFLINE_PYTHON_TAR",
        "OFFLINE_NODE_TAR",       # BL-MAC-INSTALL-NODE-BUNDLE (7/17)
        "OFFLINE_CHROMIUM_TAR",   # BL-MAC-INSTALL-CHROMIUM-BUNDLE (7/17)
        MARKER,
    ]
    for sym in required_symbols:
        if sym not in patched_text:
            print(
                f"[ERROR] patched install.sh 缺 {sym!r}. patch 逻辑有 bug.",
                file=sys.stderr,
            )
            raise SystemExit(3)
    marker_count = patched_text.count(MARKER)
    # PATCH_1 里 5 个 marker (每 offline 参数 case 各 1) + PATCH_2/3/4 各 1 + PATCH_5 (只 fi 关闭 · 0) + PATCH_6/7 各 1 = 10.
    if marker_count < 10:
        print(
            f"[ERROR] MARKER 期望 ≥10 处 (PATCH_1 5 个 · PATCH_2/3/4/6/7 各 1 · PATCH_5 关闭 fi 0 · BL-MAC-INSTALL-NODE/CHROMIUM-BUNDLE), 实际 {marker_count}.",
            file=sys.stderr,
        )
        raise SystemExit(3)


def bash_syntax_check(path: Path) -> None:
    """跑 bash -n 检查 patched 文件 shell 语法合法."""
    import subprocess
    try:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(
                f"[ERROR] patched install.sh bash 语法挂:\n{result.stderr}",
                file=sys.stderr,
            )
            raise SystemExit(4)
        print("[OK] bash -n 语法检查通过.")
    except FileNotFoundError:
        print("[WARN] bash 不在, 跳过语法检查.", file=sys.stderr)


# ─── CLI ─────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description="Patch hermes install.sh with catfish offline mode support (macOS/Linux).",
    )
    p.add_argument(
        "--input", type=Path, required=True,
        help="上游 install.sh 路径 (通常 ~/.hermes/hermes-agent/scripts/install.sh)",
    )
    p.add_argument(
        "--output", type=Path, default=None,
        help="patched 文件输出路径 (默认: <input>.patched)",
    )
    p.add_argument(
        "--check", action="store_true",
        help="只 verify 输入 + patch dry-run, 不写文件",
    )
    p.add_argument(
        "--no-sha-strict", action="store_true",
        help="SHA256 drift 只 warn, 不 exit 1 (仅 dev debug 用)",
    )
    p.add_argument(
        "--skip-syntax-check", action="store_true",
        help="跳过 bash -n 语法检查 (只 write, 不 verify)",
    )
    args = p.parse_args()

    if not args.input.is_file():
        print(f"[ERROR] input 不存在: {args.input}", file=sys.stderr)
        return 1

    original = args.input.read_text(encoding="utf-8")

    # sanity check 行数 (只 warn)
    n_lines = original.count("\n") + 1
    if n_lines < UPSTREAM_LINES_EXPECTED - 200 or n_lines > UPSTREAM_LINES_EXPECTED + 500:
        print(
            f"[WARN] install.sh 行数 {n_lines}, 期望 ~{UPSTREAM_LINES_EXPECTED}",
            file=sys.stderr,
        )

    # 已 patch 过 → 无操作幂等
    if detect_already_patched(original):
        print("[INFO] install.sh 已 patched (marker 命中). 幂等 no-op.")
        if args.check:
            verify_patched(original)
            print("[OK] verify patched install.sh 全绿.")
            return 0
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(original, encoding="utf-8")
            args.output.chmod(0o755)
            print(f"[OK] 复制 patched 文件到 {args.output}")
        return 0

    # 未 patch → 校验上游 + apply
    check_upstream_sha256(original, strict=not args.no_sha_strict)
    patched = apply_patches(original)
    verify_patched(patched)

    if args.check:
        print("[OK] --check dry-run 全绿. patched 会加 ≥10 处 marker + 5 处 --offline-* 参数 (含 --offline-node-tar + --offline-chromium-tar · BL-MAC-INSTALL-NODE/CHROMIUM-BUNDLE).")
        return 0

    out_path = args.output or args.input.with_suffix(".sh.patched")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(patched, encoding="utf-8")
    out_path.chmod(0o755)  # bash 脚本需可执行
    print(f"[OK] patched install.sh → {out_path}")

    # bash 语法 sanity check
    if not args.skip_syntax_check:
        bash_syntax_check(out_path)

    print(
        f"     Marker: {MARKER}\n"
        f"     Patches: 7 处 (param + install_uv + check_python + install_repo_open + install_repo_close + install_node + install_node_deps)\n"
        f"     dmg install handler 传 --offline-source-dir / --offline-uv / --offline-python-tar / --offline-node-tar / --offline-chromium-tar\n"
        f"     Node.js darwin binary tar 解压到 $HERMES_HOME/node/\n"
        f"     npm install skip if node_modules 已在 · Playwright chromium 解压到 ~/Library/Caches/ms-playwright/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
