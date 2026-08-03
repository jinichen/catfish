#!/usr/bin/env python3
"""Catfish install.ps1 offline patch tool (W1 BL-CATFISH-OFFLINE-INSTALL 7/11).

# 为啥独立脚本, 不塞 apply_brand_patch.py

catfish 有 `edge/hermes-fork/apply_brand_patch.py` (959 行) 做 hermes 品牌 patch.
它跑在**开发者机器**上, git hook post-merge/post-rewrite/post-checkout 自动重打.
目标是 `~/.hermes/hermes-agent/` 里的**源码 tree**.

`patches/_archive/README.md` 5/29 已明确写: catfish 长期策略 = "monkey-patch
via plugin, 不 fork hermes source". fork 面越小越好.

install.ps1 patch 场景**完全不同**:
- 目标是 **msi build artifact** (打包时 codegen, 一次性写死)
- 员工机跑的是 **msi 里预 patch 好的 install.ps1**, 跟上游没 git 关系
- 品牌 patch 的 "git hook 自动重打" 机制在员工机上根本无用

**结论**: 语义分开 → 独立脚本 → 独立生命周期 → 独立测试.

# 3 处 (实测 4 处) offline patch

1. `param()` 尾部 (line 15-60): 加 `-OfflineSourceDir` / `-OfflineUvExe` /
   `-OfflinePythonZip` 3 个可选参数. 默认空 = 走上游 canonical 网络路径, **零副作用**.

2. `Install-Uv` (line 445-491): 顶部 (在 "Installing managed uv into" Write-Info
   之前) 加 offline copy 分支 — 参数非空 → Copy-Item 内嵌 uv.exe 到
   `$HermesHome\\bin\\uv.exe` → 早 return, 跳过 astral.sh 网络下载.

3. `Test-Python` (line 577-670): 在 `uv python install` 之前 (在 "Python
   $PythonVersion not found, installing via uv..." Write-Info 之前) 加 offline
   分支 — 参数非空 → Expand-Archive 内嵌 python-3.11-embed zip 到 uv python
   cache 目录 → 复用 `uv python find` 验证.

4. `Install-Repository` (line 1252-1540): 顶部 (在 `$didUpdate = $false` 之后)
   加 offline 分支 — 参数非空 → Copy-Item 内嵌 hermes-agent 源码到 InstallDir
   + `git init` + `git remote add origin` → 早 return, 跳过 SSH/HTTPS/ZIP 3-tier
   fallback. `git remote add origin` 让未来 `hermes update` 走网络升级仍能工作
   (员工上线后有网时).

# 幂等 + drift 检测

- **detect marker**: `# CATFISH-OFFLINE-PATCH-v1` 每 patch 加, script 重跑 no-op
- **SHA256 pin**: hardcode 当前上游 install.ps1 SHA256. drift → exit 1 报错,
  提示重新 audit 上游变更. 避免上游改了 param block 我们 regex silently miss.

# 使用

    # patch:
    python3 patch_install_ps1_offline.py \\
        --input ~/.hermes/hermes-agent/scripts/install.ps1 \\
        --output ../companion-app/src-tauri/resources/windows/install.ps1

    # 只 check drift, 不写:
    python3 patch_install_ps1_offline.py --check --input .../install.ps1
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

# W2.10 (7/13 CI run #5 修): Windows Python 3.12 stdout 默认 cp1252 codec,
# script 里 print 含 `→` (U+2192) 或中文时挂 UnicodeEncodeError. 强制
# reconfigure stdout/stderr 走 UTF-8, macOS/Linux 已默认 UTF-8 无影响.
# 参考: https://docs.python.org/3/library/sys.html#sys.stdout.reconfigure (3.7+)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # 不 fatal, 兼容 Python <3.7 或非标准 stdout

# ─── 上游 pin ─────────────────────────────────────────────────

#: 当前测试通过的 install.ps1 SHA256. Bump 时必须重新 audit 7 处锚点是否稳定.
#: 计算: `shasum -a 256 <hermes-agent>/scripts/install.ps1`
UPSTREAM_SHA256 = "b5bdf0e959677de0168f8cfb5f9175c7b57adf5c4319a1c2fc9bec1f46fbdb6e"

#: 上面那个 SHA 是从**哪个上游 commit** 算出来的.
#:
#: 8/1 加。这一位存在的唯一理由是让 check_version_sync.sh 能在**本地、离线、
#: 一秒内**发现"pin 挪了但这个脚本没跟着改"。
#:
#: 7/29 的 release (1c5ca28, hermes 0.18.0 → 0.19.0) 改了 .hermes-target-version、
#: .hermes-git-tag、3 处版本号, 唯独没动这个文件。于是从那天起每一次 Windows
#: MSI 构建都在 "Patch install.ps1 offline mode" 那步红掉 —— 而那要等 clone
#: 完上游、跑到第 5 步才报, 一分钟起步; 没人天天盯 Actions 页, 就一直红着。
#:
#: 版本号那条链有 check_version_sync.sh 当场拦, 这条没有。补上之后两条一样快。
#:
#: 更新方式: 跟 edge/companion-app/.hermes-git-commit 保持一致。
UPSTREAM_COMMIT = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"

#: 上游预期行数 (rough sanity check, 不 fatal, 只 warn)
UPSTREAM_LINES_EXPECTED = 3445

#: Patch marker — script 重跑幂等靠这个
MARKER = "# CATFISH-OFFLINE-PATCH-v1"


# ─── 4 处 PowerShell 代码块 (patch payload) ─────────────────


PATCH_1_PARAM = f"""    [switch]$IncludeDesktop,

    {MARKER}: Catfish offline mode (msi CustomAction 传参, 上游默认调用不传)
    # 全部独立, 任意空即走原网络路径. 默认空 = 上游 canonical 行为, 零副作用.
    #   -OfflineSourceDir  : hermes-agent 源码**解压后**目录 (跳 git clone/ZIP · mac install.sh 用)
    #   -OfflineSourceTar  : hermes-agent 源码 **tar.gz** 压缩包 (Windows msi 用, install.ps1 先 tar xzf 再 copy)
    #                        BL-WIN-INSTALL-TAR (7/17): tauri.conf.json resources 只支持文件 (不支持目录),
    #                        msi 里只能塞 tar.gz. 若同时提供 SourceDir 优先 SourceDir (向后兼容).
    #   -OfflineUvExe      : 预下载的 uv.exe 绝对路径 (跳 astral.sh 拉)
    #   -OfflinePythonZip  : 预打包的 python-3.11 embed zip 绝对路径 (跳 uv python install)
    #   -OfflineChromiumTar: Playwright chromium **tar.gz** 压缩包 (BL-WIN-INSTALL-CHROMIUM-BUNDLE 7/17)
    #                        解压到 %LOCALAPPDATA%\\ms-playwright\\ 让 Playwright 自动 detect.
    #                        非空 → skip `npx playwright install chromium`, 员工完全 offline.
    [string]$OfflineSourceDir = "",
    [string]$OfflineSourceTar = "",
    [string]$OfflineUvExe = "",
    [string]$OfflinePythonZip = "",
    [string]$OfflineChromiumTar = ""
)"""


PATCH_2_INSTALL_UV = f"""    {MARKER}: Catfish offline — copy embedded uv.exe, skip astral.sh
    if ($OfflineUvExe -and (Test-Path $OfflineUvExe)) {{
        Write-Info "Catfish offline: copying uv.exe from $OfflineUvExe"
        New-Item -ItemType Directory -Path (Join-Path $HermesHome "bin") -Force | Out-Null
        Copy-Item -LiteralPath $OfflineUvExe -Destination $managedUv -Force
        $script:UvCmd = $managedUv
        $version = & $managedUv --version
        Write-Success "Managed uv installed from offline bundle ($version)"
        return $true
    }}

    Write-Info "Installing managed uv into $HermesHome\\bin ..."
"""


# uv python cache 路径 blocker W1-3 mitigation:
# 走 UV_PYTHON_INSTALL_DIR 环境变量 (uv 0.4+ 官方支持) 而不是猜路径.
# uv doc: https://docs.astral.sh/uv/reference/environment/#uv_python_install_dir
# 让 uv 自己决定 cache root, 我们只 push zip 内容进去. build 前不用 spike.
PATCH_3_TEST_PYTHON = f"""    {MARKER}: Catfish offline — expand embedded python zip, skip uv install (network)
    if ($OfflinePythonZip -and (Test-Path $OfflinePythonZip)) {{
        Write-Info "Catfish offline: expanding python from $OfflinePythonZip"
        # 走 UV_PYTHON_INSTALL_DIR 让 uv 自己识别 (不用猜 %APPDATA% vs %LOCALAPPDATA%)
        $uvPythonRoot = if ($env:UV_PYTHON_INSTALL_DIR) {{ $env:UV_PYTHON_INSTALL_DIR }}
                       else {{ Join-Path $env:LOCALAPPDATA "uv\\python" }}
        New-Item -ItemType Directory -Path $uvPythonRoot -Force | Out-Null
        try {{
            Expand-Archive -Path $OfflinePythonZip -DestinationPath $uvPythonRoot -Force
        }} catch {{
            Write-Warn "Catfish offline: expand python zip failed: $_ - 回退网络路径"
        }}
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {{
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python installed from offline bundle: $ver"
            return $true
        }}
        Write-Warn "Catfish offline: python bundle expanded but uv find failed - 回退网络路径"
    }}

    # Python not found -- use uv to install it (no admin needed!)
    Write-Info "Python $PythonVersion not found, installing via uv..."
"""


PATCH_4_INSTALL_REPO = f"""    $didUpdate = $false

    {MARKER}: Catfish offline — copy pre-extracted hermes-agent source (mac install.sh 用) 或 tar.gz (Windows msi 用)
    # BL-WIN-INSTALL-TAR (7/17 抓 Windows Error 1722 根因): 老代码只支持 -OfflineSourceDir 指
    # 向已解压目录. 但 msi 里只能塞 tar.gz (tauri.conf.json resources 只支持单文件, 不支持
    # 目录级 include), 所以 Windows install.ps1 收 -OfflineSourceTar tar.gz 后先解压再 Copy.
    # 优先级: SourceDir (mac) > SourceTar (Windows).
    $effectiveSourceDir = $null
    $tempExtractRoot = $null
    if ($OfflineSourceDir -and (Test-Path $OfflineSourceDir)) {{
        Write-Info "Catfish offline: using pre-extracted source $OfflineSourceDir"
        $effectiveSourceDir = $OfflineSourceDir
    }} elseif ($OfflineSourceTar -and (Test-Path $OfflineSourceTar)) {{
        Write-Info "Catfish offline: extracting hermes-agent from tar $OfflineSourceTar"
        $tempExtractRoot = Join-Path $env:TEMP ("catfish-hermes-extract-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $tempExtractRoot | Out-Null
        try {{
            tar -xzf $OfflineSourceTar -C $tempExtractRoot
            if ($LASTEXITCODE -ne 0) {{ throw "tar 解压 exit=$LASTEXITCODE" }}
        }} catch {{
            Write-Warn "Catfish offline: tar 解压挂: $_"
            Remove-Item -Recurse -Force $tempExtractRoot -ErrorAction SilentlyContinue
            throw
        }}
        # tar.gz 结构 CircleCI 打包时是 hermes-agent-src/ 顶级目录 (config.yml Pack step)
        $extractedSrc = Join-Path $tempExtractRoot "hermes-agent-src"
        if (Test-Path $extractedSrc) {{
            $effectiveSourceDir = $extractedSrc
        }} else {{
            # fallback: tar 里不是 hermes-agent-src/ 顶级, 用 extract root 自身
            Write-Warn "Catfish offline: tar 里无 hermes-agent-src/ 顶层, 用 $tempExtractRoot"
            $effectiveSourceDir = $tempExtractRoot
        }}
    }}

    if ($effectiveSourceDir) {{
        Write-Info "Catfish offline: copying hermes-agent from $effectiveSourceDir to $InstallDir"
        if (Test-Path $InstallDir) {{
            $backupDir = "$InstallDir.replaced-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Move-Item -LiteralPath $InstallDir -Destination $backupDir -ErrorAction SilentlyContinue
        }}
        New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction SilentlyContinue | Out-Null
        Copy-Item -LiteralPath $effectiveSourceDir -Destination $InstallDir -Recurse -Force
        # git init 让上游 update 路径能工作 (未来员工有网时 hermes update)
        Push-Location $InstallDir
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        git -c windows.appendAtomically=false init 2>$null
        git -c windows.appendAtomically=false config core.autocrlf false 2>$null
        git remote add origin $RepoUrlHttps 2>$null
        Pop-Location
        # 清理 tar 临时解压目录
        if ($tempExtractRoot -and (Test-Path $tempExtractRoot)) {{
            Remove-Item -Recurse -Force $tempExtractRoot -ErrorAction SilentlyContinue
        }}
        Write-Success "hermes-agent installed from offline bundle"
        # 跳过下面的 update / clone 3-tier fallback
        return
    }}

"""


# BL-WIN-INSTALL-NPM-OFFLINE (7/17): 员工机完全无公网, npm install 挂. 加 2 处 patch.
#
# 处 5 · Install-AgentBrowser (line ~371): 全局 npm 装 agent-browser + camofox-browser.
# CircleCI 上 `npm pack` 生成 .tgz 打进 hermes-agent-src\node-globals\, 装到 msi 里.
# install.ps1 改成先查 $HermesHome\hermes-agent\node-globals\, 有 .tgz 就装本地, 无 fallback registry.
PATCH_5_NPM_GLOBAL = f"""    {MARKER}: Catfish offline — 装本地 .tgz (agent-browser + camofox-browser)
    $offlineTgzDir = Join-Path $HermesHome "hermes-agent\\node-globals"
    if (Test-Path $offlineTgzDir) {{
        $tgzFiles = @(Get-ChildItem -Path $offlineTgzDir -Filter "*.tgz" -ErrorAction SilentlyContinue)
        if ($tgzFiles.Count -ge 1) {{
            Write-Info "Catfish offline: npm globals from local .tgz ($($tgzFiles.Count) 个)"
            $tgzPaths = $tgzFiles | ForEach-Object {{ $_.FullName }}
            & $npm install -g --prefix $prefixDir --silent --ignore-scripts @tgzPaths 2>&1 | Tee-Object -FilePath $npmLog | Out-Null
        }} else {{
            Write-Warn "Catfish offline: $offlineTgzDir 无 .tgz - fallback registry (员工无公网必挂)"
            & $npm install -g --prefix $prefixDir --silent --ignore-scripts "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2" 2>&1 | Tee-Object -FilePath $npmLog | Out-Null
        }}
    }} else {{
        & $npm install -g --prefix $prefixDir --silent --ignore-scripts "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2" 2>&1 | Tee-Object -FilePath $npmLog | Out-Null
    }}
"""


# 处 6 · _Run-NpmInstall (line ~2196): 逐目录本地 npm install (Browser tools + TUI).
# CircleCI 上 pre-install 生成 node_modules 打进 hermes-agent-src, 装到 msi 里.
# install.ps1 函数开头查 $installDir\node_modules\ 已存在直接 return $true, 跳过 npm install.
PATCH_6_NPM_LOCAL = f"""    function _Run-NpmInstall([string]$label, [string]$installDir, [string]$logPath, [string]$npmPath) {{
        {MARKER}: Catfish offline — node_modules 已在 (msi 打了 CircleCI pre-install), skip
        if (Test-Path (Join-Path $installDir "node_modules")) {{
            # ⚠ 必须写 ${{label}} 不能写 $label —— PowerShell 里 `$label:` 会被当成
            # 驱动器/命名空间限定符 (就像 $env:PATH), 冒号后面跟空格直接是**解析错误**:
            #   变量引用无效。':' 后面的变量名称字符无效。
            # 这是 parse 阶段的错, 整个 install.ps1 一行都执行不了, msi 的
            # CustomAction 当场失败 → 员工看到"Windows Installer 程序包有问题"。
            # 7/17 (79cbab9) 写下这行起, Windows msi 装不上装了两周多没人发现 ——
            # 因为 CI 只是把文件打进包, 不解析它, 构建一路绿。
            Write-Info "${{label}}: node_modules already present (Catfish offline bundle), skip npm install"
            Write-Success "$label dependencies already installed (offline)"
            return $true
        }}
        Push-Location $installDir"""


# 处 7 · Playwright Chromium 装 (line ~2395): npx playwright install chromium 下载 350MB.
# 员工无公网必挂. 用户拍板打进 msi (BL-WIN-INSTALL-CHROMIUM-BUNDLE 7/17).
#
# 策略:
#   若 -OfflineChromiumTar 传 → tar 解压到 %LOCALAPPDATA%\ms-playwright\ 让 Playwright 自动 detect · skip npx playwright install
#   若 只 -OfflineSourceTar/Dir 无 ChromiumTar → 短路 skip (fallback · 员工需手动装 chromium)
#   若 无 offline 参数 → 原上游行为
#
# 避免嵌套括号 · 老代码整个 block (包括 if/else/finally) 100% 保留原样, 只在前面加解压 + 短路变量.
PATCH_7_PLAYWRIGHT_CHROMIUM = f"""        $browserNpmOk = _Run-NpmInstall "Browser tools" $InstallDir $browserLog $npmExe

        {MARKER}: Catfish offline — 若 -OfflineChromiumTar 传, tar 解压到 %LOCALAPPDATA%\\ms-playwright\\ (Playwright 默认路径, 自动 detect)
        $catfishSkipChromium = $false
        if ($OfflineChromiumTar -and (Test-Path $OfflineChromiumTar)) {{
            $chromiumDest = Join-Path $env:LOCALAPPDATA "ms-playwright"
            Write-Info "Catfish offline: 解压 Playwright Chromium bundle 到 $chromiumDest"
            New-Item -ItemType Directory -Force -Path $chromiumDest -ErrorAction SilentlyContinue | Out-Null
            try {{
                tar -xzf $OfflineChromiumTar -C $chromiumDest
                if ($LASTEXITCODE -ne 0) {{ throw "tar chromium exit=$LASTEXITCODE" }}
                Write-Success "Playwright Chromium installed from offline bundle"
                $catfishSkipChromium = $true
            }} catch {{
                Write-Warn "Catfish offline: chromium tar 解压挂: $_ - fallback npx playwright install (员工无公网必挂)"
            }}
        }} elseif ($OfflineSourceTar -or $OfflineSourceDir) {{
            Write-Info "Catfish offline: 无 -OfflineChromiumTar, skip Playwright Chromium 装 (browser_* tools 手动装 chromium 后可用)"
            $catfishSkipChromium = $true
        }}

        # Install Playwright Chromium (mirrors scripts/install.sh behaviour for
        # Linux).  Without this, tools/browser_tool.py::check_browser_requirements
        # returns False (no Chromium under %LOCALAPPDATA%\\ms-playwright), and the
        # browser_* tools are silently filtered out of the agent's tool schema.
        # System Chrome at "C:\\Program Files\\Google\\Chrome\\..." is NOT used by
        # agent-browser -- it expects a Playwright-managed Chromium.
        if ($browserNpmOk -and -not $catfishSkipChromium) {{"""


# ─── 6 处 anchor (完全精确的 unique string) ────────────────


ANCHORS = {
    "param": (
        "    [switch]$IncludeDesktop\n)",  # BEFORE
        PATCH_1_PARAM,                       # AFTER (含 marker)
    ),
    "install_uv": (
        '    Write-Info "Installing managed uv into $HermesHome\\bin ..."\n',
        PATCH_2_INSTALL_UV,
    ),
    "test_python": (
        '    # Python not found -- use uv to install it (no admin needed!)\n'
        '    Write-Info "Python $PythonVersion not found, installing via uv..."\n',
        PATCH_3_TEST_PYTHON,
    ),
    "install_repository": (
        "    $didUpdate = $false\n\n",
        PATCH_4_INSTALL_REPO,
    ),
    "npm_global": (
        '    & $npm install -g --prefix $prefixDir --silent --ignore-scripts "agent-browser@^0.26.0" "@askjo/camofox-browser@^1.5.2" 2>&1 | Tee-Object -FilePath $npmLog | Out-Null\n',
        PATCH_5_NPM_GLOBAL,
    ),
    "npm_local_helper": (
        '    function _Run-NpmInstall([string]$label, [string]$installDir, [string]$logPath, [string]$npmPath) {\n        Push-Location $installDir',
        PATCH_6_NPM_LOCAL,
    ),
    "playwright_chromium": (
        '        $browserNpmOk = _Run-NpmInstall "Browser tools" $InstallDir $browserLog $npmExe\n'
        '\n'
        '        # Install Playwright Chromium (mirrors scripts/install.sh behaviour for\n'
        '        # Linux).  Without this, tools/browser_tool.py::check_browser_requirements\n'
        '        # returns False (no Chromium under %LOCALAPPDATA%\\ms-playwright), and the\n'
        '        # browser_* tools are silently filtered out of the agent\'s tool schema.\n'
        '        # System Chrome at "C:\\Program Files\\Google\\Chrome\\..." is NOT used by\n'
        '        # agent-browser -- it expects a Playwright-managed Chromium.\n'
        '        if ($browserNpmOk) {',
        PATCH_7_PLAYWRIGHT_CHROMIUM,
    ),
}


# ─── 核心 patch 函数 ──────────────────────────────────────


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_already_patched(text: str) -> bool:
    """是否已 patched (幂等). marker 命中即认为已 patch."""
    return MARKER in text


def check_upstream_sha256(text: str, *, strict: bool = True) -> str:
    """校验上游 install.ps1 是否是我们 audit 过的版本.

    Returns actual SHA256. Raises SystemExit(1) if strict and mismatch.
    """
    actual = sha256_of(text)
    if actual == UPSTREAM_SHA256:
        return actual
    msg = (
        f"[CATFISH-OFFLINE-PATCH] SHA256 drift!\n"
        f"  expected: {UPSTREAM_SHA256}\n"
        f"  actual:   {actual}\n"
        f"上游 install.ps1 变了 (可能新参数 / anchor 挪位置). 必须:\n"
        f"  1. 重新 audit 4 处 anchor 是否稳定\n"
        f"  2. 更新脚本顶部 UPSTREAM_SHA256\n"
        f"  3. verify 3 处 offline PowerShell 分支跟上游 flow 兼容\n"
    )
    if strict:
        print(msg, file=sys.stderr)
        raise SystemExit(1)
    print(f"[WARN] {msg}", file=sys.stderr)
    return actual


def apply_patches(text: str) -> str:
    """4 处 anchor 逐一替换, 每处 must 命中 1 次 (不多不少)."""
    result = text
    for name, (before, after) in ANCHORS.items():
        count = result.count(before)
        if count == 0:
            print(
                f"[ERROR] anchor {name!r} 找不到 (0 命中). 上游可能改了此段代码.",
                file=sys.stderr,
            )
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
    """patched 输出 sanity check — 3 个 -Offline* 参数 + 4 处 marker 都在."""
    required_symbols = [
        "$OfflineSourceDir",
        "$OfflineSourceTar",       # BL-WIN-INSTALL-TAR (7/17): Windows msi 新增 tar 输入
        "$OfflineUvExe",
        "$OfflinePythonZip",
        "$OfflineChromiumTar",     # BL-WIN-INSTALL-CHROMIUM-BUNDLE (7/17): Playwright chromium tar 输入
        MARKER,
    ]
    for sym in required_symbols:
        if sym not in patched_text:
            print(
                f"[ERROR] patched install.ps1 缺 {sym!r}. patch 逻辑有 bug.",
                file=sys.stderr,
            )
            raise SystemExit(3)
    marker_count = patched_text.count(MARKER)
    if marker_count != 7:
        print(
            f"[ERROR] MARKER 期望 7 处 (每 patch 1 处 · BL-WIN-INSTALL-TAR/NPM-OFFLINE/CHROMIUM-SKIP), 实际 {marker_count}.",
            file=sys.stderr,
        )
        raise SystemExit(3)


# ─── CLI ─────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description="Patch hermes install.ps1 with catfish offline mode support.",
    )
    p.add_argument(
        "--input", type=Path, required=True,
        help="上游 install.ps1 路径 (通常 ~/.hermes/hermes-agent/scripts/install.ps1)",
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
    args = p.parse_args()

    if not args.input.is_file():
        print(f"[ERROR] input 不存在: {args.input}", file=sys.stderr)
        return 1

    original = args.input.read_text(encoding="utf-8")

    # sanity check 行数 (只 warn)
    n_lines = original.count("\n") + 1
    if n_lines < UPSTREAM_LINES_EXPECTED - 100 or n_lines > UPSTREAM_LINES_EXPECTED + 500:
        print(
            f"[WARN] install.ps1 行数 {n_lines}, 期望 ~{UPSTREAM_LINES_EXPECTED}",
            file=sys.stderr,
        )

    # 已 patch 过 → 无操作幂等
    if detect_already_patched(original):
        print("[INFO] install.ps1 已 patched (marker 命中). 幂等 no-op.")
        if args.check:
            verify_patched(original)
            print("[OK] verify patched install.ps1 全绿.")
            return 0
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(original, encoding="utf-8")
            print(f"[OK] 复制 patched 文件到 {args.output}")
        return 0

    # 未 patch → 校验上游 + apply
    check_upstream_sha256(original, strict=not args.no_sha_strict)
    patched = apply_patches(original)
    verify_patched(patched)

    if args.check:
        print("[OK] --check dry-run 全绿. patched 会加 7 处 marker + 5 处 -Offline* 参数 (BL-WIN-INSTALL-TAR/NPM-OFFLINE/CHROMIUM-BUNDLE).")
        return 0

    out_path = args.output or args.input.with_suffix(".ps1.patched")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(patched, encoding="utf-8")
    print(f"[OK] patched install.ps1 → {out_path}")
    print(
        f"     Marker: {MARKER}\n"
        f"     Patches: 7 处 (param + Install-Uv + Test-Python + Install-Repository + npm-global + npm-local-helper + playwright-chromium-bundle)\n"
        f"     msi CustomAction 传 -OfflineSourceTar / -OfflineUvExe / -OfflinePythonZip / -OfflineChromiumTar (+ -OfflineSourceDir 保留 mac 兼容)\n"
        f"     npm global .tgz 从 $HermesHome\\hermes-agent\\node-globals\\ 自动拾取\n"
        f"     npm local 若 node_modules\\ 已在自动 skip\n"
        f"     Playwright chromium 若 -OfflineChromiumTar 传 · tar 解压到 %LOCALAPPDATA%\\ms-playwright\\ (100% offline)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
