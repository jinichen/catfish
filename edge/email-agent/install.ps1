# =============================================================
# catfish-email installer (Windows PowerShell 版)
# =============================================================
#
# 对应 install.sh，做同样两件事：
#   1. pip install -e 这个包到 Hermes 的 venv（hermes terminal tool 调
#      `catfish-email ...` 能直接跑）
#   2. 把 SKILL 目录挂到 hermes 的 skills\productivity\ 让 hermes 加载
#
# 用法（不需要管理员权限，幂等）：
#   powershell -ExecutionPolicy Bypass -File edge\email-agent\install.ps1
#   .\install.ps1 -HermesHome D:\hermes        # 非默认位置
#
# ── 跟 install.sh 的四处平台差异 ──────────────────────────────────
#
# 1. hermes 装在哪
#    Unix:    $HOME/.hermes
#    Windows: %LOCALAPPDATA%\hermes  —— 见 companion-app/src-tauri/resources/
#             windows/_phase1_win_install_hermes.ps1:99，per-user 安装不要管理员权限
#
# 2. venv 里可执行文件的位置
#    Unix:    venv/bin/catfish-email
#    Windows: venv\Scripts\catfish-email.exe   （pip console_script 带 .exe）
#
# 3. 怎么让 PATH 找得到
#    Unix:    软链到 ~/.local/bin（macOS 默认 PATH 含此目录）
#    Windows: 没有这个约定，改成把 venv\Scripts 写进用户 PATH
#             （跟 onboarding\install-catfish.ps1 第 4 步同款做法）
#
# 4. skill 怎么挂
#    Unix:    ln -sfn（符号链接）
#    Windows: Junction —— 目录联接，**不需要管理员权限也不需要开发者模式**，
#             而 New-Item -ItemType SymbolicLink 两者都要。装不上时退化成复制。
#
# ── 一处没有验证过的路径 ──────────────────────────────────────────
#
# hermes 在 Windows 上从哪读 skills，这个仓库里没有先例：所有 installer
# （email / local-search / communication-coach）都写死 $HOME/.hermes/skills/
# productivity，没有任何 Windows 版本可对照，hermes 本体源码也不在仓库里。
#
# 这里按 _phase1_win_install_hermes.ps1 的 $HermesHome 约定推导成
# $HermesHome\skills\productivity（Unix 上 skills/ 和 hermes-agent/ 就是同级）。
# 装完会把解析出来的路径打出来 —— 如果 hermes 重启后 banner 里没出现
# catfish-email，八成就是这条路径不对，用 -SkillsDir 指到正确位置再跑一次。

[CmdletBinding()]
param(
    [string]$HermesHome,
    [string]$SkillsDir
)

$ErrorActionPreference = "Stop"

function Ok($m)   { Write-Host "OK   $m" -ForegroundColor Green }
function Info($m) { Write-Host "->   $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "警告 $m" -ForegroundColor DarkYellow }
function Fail($m) { Write-Host "错误 $m" -ForegroundColor Red }

Write-Host "=== catfish-email installer (Windows) ===`n" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $HermesHome) {
    $HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME }
                  else { Join-Path $env:LOCALAPPDATA "hermes" }
}
$HermesVenv    = Join-Path $HermesHome "hermes-agent\venv"
$HermesVenvPy  = Join-Path $HermesVenv "Scripts\python.exe"
$VenvScripts   = Join-Path $HermesVenv "Scripts"
$CatfishEmail  = Join-Path $VenvScripts "catfish-email.exe"

if (-not $SkillsDir) { $SkillsDir = Join-Path $HermesHome "skills\productivity" }
$SkillSrc = Join-Path $ScriptDir "hermes-skill\catfish-email"
$SkillDst = Join-Path $SkillsDir "catfish-email"

Write-Host "    HermesHome : $HermesHome"
Write-Host "    venv python: $HermesVenvPy"
Write-Host "    skills dir : $SkillsDir  (没验证过，见脚本头注释)`n"

# ---------- 1. 装 Python 包到 Hermes venv ----------

if (-not (Test-Path $HermesVenvPy)) {
    Fail "找不到 Hermes 的 venv Python: $HermesVenvPy"
    Write-Host "     装 Hermes 时通常会建在这。检查 $HermesHome\hermes-agent\ 是否存在。"
    Write-Host "     装在别处就用: .\install.ps1 -HermesHome <路径>"
    exit 1
}

# venv 可能没 pip（Hermes 某些版本如此），先 bootstrap
& $HermesVenvPy -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Info "Hermes venv 没 pip，ensurepip bootstrap"
    & $HermesVenvPy -m ensurepip --upgrade --default-pip
    if ($LASTEXITCODE -ne 0) {
        Fail "ensurepip 失败（可能 pyvenv.cfg 禁了）。手工装 pip 后再跑。"
        exit 1
    }
}
Ok "pip 可用"

# 已经是 editable 装好且指向本目录 → 源码改动直接生效，不用重装。
# （install.sh 8/6 加的同款判断：那次代理挂了装不动，其实根本不需要装。）
$ProbeCode = @'
import pathlib
try:
    import catfish_email
    print(pathlib.Path(catfish_email.__file__).resolve().parent.parent.parent)
except Exception:
    print("")
'@
$Already = ""
try {
    $Already = ($ProbeCode | & $HermesVenvPy - 2>$null | Out-String).Trim()
} catch {
    $Already = ""
}

$SkipPip = $false
if ($Already -and ((Resolve-Path -LiteralPath $Already -ErrorAction SilentlyContinue).Path -eq
                   (Resolve-Path -LiteralPath $ScriptDir).Path)) {
    Ok "已是 editable 安装且指向本目录 —— 跳过 pip install"
    Write-Host "     ($Already)"
    $SkipPip = $true
}

if (-not $SkipPip) {
    Info "pip install -e 到 Hermes venv"
    & $HermesVenvPy -m pip install -e $ScriptDir --quiet
    if ($LASTEXITCODE -ne 0) {
        # 跟 install.sh 一样的兜底：代理配了但没跑起来时，pip 会为了新建隔离
        # 构建环境去下 setuptools 而卡住。这个包是纯 Python 无编译，
        # --no-build-isolation 直接用 venv 现有的 setuptools，全程不联网。
        Warn "常规安装失败 —— 试无网络路径 (--no-build-isolation)"
        $saved = @{}
        foreach ($v in @("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy")) {
            $saved[$v] = [Environment]::GetEnvironmentVariable($v)
            [Environment]::SetEnvironmentVariable($v, $null)
        }
        & $HermesVenvPy -m pip install -e $ScriptDir --no-build-isolation --no-index --quiet
        $code = $LASTEXITCODE
        foreach ($v in $saved.Keys) { [Environment]::SetEnvironmentVariable($v, $saved[$v]) }
        if ($code -ne 0) {
            Fail "还是装不上。两条路："
            Write-Host "     1. 代理起了再跑"
            Write-Host "     2. 手工装: & '$HermesVenvPy' -m pip install -e '$ScriptDir' --no-build-isolation"
            exit 1
        }
    }
    Ok "装好 catfish-email 包"
}

# ---------- 2. 验证 CLI 能跑 ----------

$CliOk = $false
if (Test-Path $CatfishEmail) {
    & $CatfishEmail --help *> $null
    if ($LASTEXITCODE -eq 0) {
        Ok "CLI 可调 (entry point): $CatfishEmail"
        $CliOk = $true
    }
}
if (-not $CliOk) {
    & $HermesVenvPy -m catfish_email --help *> $null
    if ($LASTEXITCODE -eq 0) {
        Warn "entry point 不可用，但 python -m catfish_email 能跑"
        Write-Host "     Companion 找的是 catfish-email.exe，建议排查 pip 的 console_scripts"
    } else {
        Warn "CLI 装上了但跑不通，看上面的 pip 日志"
    }
}

# ---------- 3. 让 PATH 找得到 ----------
#
# hermes 的 terminal tool 是 spawn 一个 shell 去跑 `catfish-email ...`，
# 所以必须在 PATH 里。Companion 自己不依赖这个 —— 它走
# catfish_paths::catfish_email_bin()，会直接去 venv 的 Scripts 找。

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$VenvScripts*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$VenvScripts", "User")
    Ok "已把 $VenvScripts 加进用户 PATH（要开新窗口才生效）"
} else {
    Ok "用户 PATH 已包含 $VenvScripts"
}

# ---------- 4. 挂 SKILL 目录 ----------

if (-not (Test-Path (Join-Path $SkillSrc "SKILL.md"))) {
    Warn "找不到 $SkillSrc\SKILL.md，跳过 skill 装载"
} else {
    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
    if (Test-Path $SkillDst) { Remove-Item -Recurse -Force $SkillDst }
    try {
        # Junction 不需要管理员权限也不需要开发者模式；SymbolicLink 两者都要。
        New-Item -ItemType Junction -Path $SkillDst -Target $SkillSrc | Out-Null
        Ok "已装 skill (junction): $SkillDst -> $SkillSrc"
    } catch {
        Copy-Item -Recurse -Force $SkillSrc $SkillDst
        Warn "junction 建不了，改成复制: $SkillDst"
        Write-Host "     注意：复制是快照，改了 SKILL.md 要重跑这个脚本"
    }
}

Write-Host ""
Write-Host "=== 装好了 ===" -ForegroundColor Green
@"

下一步:
  1. 退出 hermes，重开一个新 PowerShell（PATH 才生效），重启 hermes
  2. banner 的 productivity 段应该出现 catfish-email
  3. 测试: 在 hermes 里发"今天有什么邮件没回"

banner 里没出现 catfish-email?
  多半是 skills 目录不对（这条路径没在真 Windows 上验证过，见脚本头注释）。
  找到 hermes 实际读的 skills 目录后:
      .\install.ps1 -SkillsDir <正确路径>

命令行自查:
  catfish-email accounts --human
  catfish-email list --human --limit=3

注意 Windows 上目前只有 Outlook 只读四件套（列 / 读 / 搜 / 账号）。
起草、发送、删除、标已读还没实现 —— 见 adapters\outlook_win.py。

卸载:
  & "$HermesVenvPy" -m pip uninstall catfish-email -y
  Remove-Item -Recurse "$SkillDst"
"@ | Write-Host
