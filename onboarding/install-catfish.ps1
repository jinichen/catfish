# =============================================================
# 鲶鱼 Catfish · 员工一键装（Windows PowerShell 版）
# =============================================================
#
# 装的东西跟 install-catfish.sh 完全一致：
#   - catfish-search          本地文件全文搜索 CLI
#   - catfish-search daemon   后台增量索引（可选）
#   - Hermes MCP server       让 Hermes 自动调本地搜索
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File onboarding\install-catfish.ps1
#   .\onboarding\install-catfish.ps1 -Yes           # 免交互
#   .\onboarding\install-catfish.ps1 -SkipIndex -SkipDaemon
#
# 不需要管理员权限。幂等。

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$SkipIndex,
    [switch]$SkipDaemon
)

$ErrorActionPreference = "Stop"

# ---------- 小工具 ----------

function Step($n, $msg) { Write-Host "[$n/8] $msg" -ForegroundColor Yellow }
function Ok($msg)       { Write-Host "    OK $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    警告 $msg" -ForegroundColor DarkYellow }
function Err($msg)      { Write-Host "    错误 $msg" -ForegroundColor Red }

function Confirm-Step {
    param([string]$Prompt, [string]$Default = "Y")
    if ($Yes) { return $true }
    $suffix = if ($Default -eq "Y") { "[Y/n]" } else { "[y/N]" }
    $reply = Read-Host "    $Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $Default -eq "Y" }
    return $reply -match '^[Yy]'
}

Write-Host "鲶鱼 Catfish 员工工具一键装（Windows）`n" -ForegroundColor Cyan

# ---------- 1. Preflight ----------

Step 1 "检查前置条件"

# 找 Python 3.10+，优先用 py 启动器的新版本
$PythonBin = $null
foreach ($ver in @("3.12", "3.11", "3.10")) {
    $out = & py "-$ver" --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $PythonBin = "py -$ver"
        Ok "Python $ver (py -$ver)"
        break
    }
}
if (-not $PythonBin) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        $parts = $ver -split '\.'
        if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10)) {
            $PythonBin = "python"
            Ok "Python $ver (python)"
        }
    }
}
if (-not $PythonBin) {
    Err "找不到 Python 3.10+"
    Write-Host "    到 https://www.python.org/downloads/ 下载 Python 3.12，"
    Write-Host "    安装时务必勾上 'Install launcher for all users'"
    exit 1
}

# 定位 catfish 项目根
# 不依赖仓库外的 catfish-design.md：该文件不是 GitHub 仓库内容，不能作为安装前置。
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$CatfishRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
if (-not (Test-Path (Join-Path $CatfishRoot "README.md")) -or
    -not (Test-Path (Join-Path $CatfishRoot "edge\local-search\pyproject.toml"))) {
    Err "找不到 catfish 项目根或 edge\local-search（请从完整仓库运行脚本）"
    exit 1
}
Ok "catfish 项目根：$CatfishRoot"

# Hermes 装没装
$HasHermes = $false
if (Get-Command hermes -ErrorAction SilentlyContinue) {
    $HasHermes = $true
    Ok "Hermes Agent 已安装"
} else {
    Warn "Hermes Agent 未安装 —— 跳过 MCP 注册，只装 catfish-search CLI"
}

# ---------- 2. 员工 venv ----------

Step 2 "准备员工 venv %USERPROFILE%\.catfish\venv"

$CatfishHome = Join-Path $env:USERPROFILE ".catfish"
$CatfishVenv = Join-Path $CatfishHome "venv"
New-Item -ItemType Directory -Force -Path $CatfishHome | Out-Null

if (-not (Test-Path $CatfishVenv)) {
    & cmd /c "$PythonBin -m venv `"$CatfishVenv`""
    Ok "创建 $CatfishVenv"
} else {
    $venvPy = Join-Path $CatfishVenv "Scripts\python.exe"
    & $venvPy --version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Warn "venv 损坏，重建"
        Remove-Item -Recurse -Force $CatfishVenv
        & cmd /c "$PythonBin -m venv `"$CatfishVenv`""
    }
    Ok "复用已有 venv"
}

$VenvPy  = Join-Path $CatfishVenv "Scripts\python.exe"
$VenvPip = Join-Path $CatfishVenv "Scripts\pip.exe"

# ---------- 3. pip install ----------

Step 3 "装 catfish-local-search + 依赖"

& $VenvPip install -q -U pip
$LocalSearchDir = Join-Path $CatfishRoot "edge\local-search"
if (-not (Test-Path (Join-Path $LocalSearchDir "pyproject.toml"))) {
    Err "$LocalSearchDir\pyproject.toml 不存在（项目结构问题）"
    exit 1
}
& $VenvPip install -q -e "$LocalSearchDir[all,mcp]"
if ($LASTEXITCODE -ne 0) {
    Err "pip install 失败，检查网络或公司内网 PyPI mirror"
    exit 1
}
Ok "catfish-search, catfish-search-mcp 装好"

# ---------- 4. 加到 PATH ----------

Step 4 "把 venv\Scripts 加到用户 PATH"

$ScriptsDir = Join-Path $CatfishVenv "Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$ScriptsDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$ScriptsDir", "User")
    Ok "已加到 Path（需要开一个新 PowerShell 才生效）"
} else {
    Ok "Path 已包含 $ScriptsDir"
}

# ---------- 5. 生成默认配置 ----------

Step 5 "初始化 ~/.catfish/search-scope.yaml"

$ScopeFile = Join-Path $CatfishHome "search-scope.yaml"
if (Test-Path $ScopeFile) {
    Ok "已存在，保留员工自定义"
} else {
    & (Join-Path $ScriptsDir "catfish-search.exe") status 2>$null | Out-Null
    if (Test-Path $ScopeFile) {
        Ok "已生成默认 include（Documents / Desktop / Downloads）"
    } else {
        Warn "配置生成失败，请手动跑 catfish-search config"
    }
}

# ---------- 6. 首次 index ----------

Step 6 "首次全量索引"

if ($SkipIndex) {
    Ok "跳过（-SkipIndex）"
} elseif (Confirm-Step "现在跑一次全量索引吗？（1~10 分钟）" "Y") {
    & (Join-Path $ScriptsDir "catfish-search.exe") index
    Ok "索引完成"
} else {
    Ok "跳过，以后手动跑 catfish-search index"
}

# ---------- 7. Hermes MCP ----------

Step 7 "装 Hermes MCP server + skill 文档"

if (-not $HasHermes) {
    Ok "跳过（Hermes 未装）"
} else {
    # Windows 上目前没有 install.ps1 for hermes-skill，手工完成等价逻辑：
    $HermesConfig = Join-Path $env:USERPROFILE ".hermes\config.yaml"
    $McpBin       = Join-Path $ScriptsDir "catfish-search-mcp.exe"
    if (-not (Test-Path $McpBin)) {
        Warn "找不到 $McpBin，跳过 MCP 注册"
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path $HermesConfig) | Out-Null
        if (-not (Test-Path $HermesConfig)) { New-Item -ItemType File -Path $HermesConfig | Out-Null }

        # 用 venv Python 读写 yaml，保持跟 sh 版一致
        $regScript = @"
import sys, yaml
from pathlib import Path
cfg = Path(sys.argv[1])
name = sys.argv[2]
cmd  = sys.argv[3]
text = cfg.read_text(encoding='utf-8') if cfg.exists() else ''
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict): data = {}
servers = data.setdefault('mcp_servers', {}) or {}
old = servers.get(name)
servers[name] = {
    'command': cmd,
    'args': [],
    'description': 'Catfish local file full-text search (FTS5, offline)',
    'enabled': True,
}
data['mcp_servers'] = servers
cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding='utf-8')
print(('updated' if old else 'added') + ' ' + name)
"@
        $tmp = [IO.Path]::GetTempFileName() + ".py"
        $regScript | Set-Content -Path $tmp -Encoding utf8
        & $VenvPy $tmp $HermesConfig "catfish-local-search" $McpBin
        Remove-Item $tmp

        # skill 文档软链 —— Windows 没有 symlink 不要求管理员，改用 junction / 复制
        $HermesSkillDir = Join-Path $env:USERPROFILE ".hermes\skills\productivity\catfish-local-search"
        $SrcSkillDir    = Join-Path $LocalSearchDir "hermes-skill\catfish-local-search"
        New-Item -ItemType Directory -Force -Path (Split-Path $HermesSkillDir) | Out-Null
        if (Test-Path $HermesSkillDir) { Remove-Item -Recurse -Force $HermesSkillDir }
        # mklink /J 做目录联接，不需要管理员
        cmd /c mklink /J "$HermesSkillDir" "$SrcSkillDir" | Out-Null
        Ok "MCP + skill 装好；/exit 重启 Hermes 后生效"
    }
}

# ---------- 8. Daemon ----------

Step 8 "后台 watcher"

if ($SkipDaemon) {
    Ok "跳过（-SkipDaemon）"
} elseif (Confirm-Step "装后台 watcher 吗？（走 Windows 任务计划，崩溃自动重启）" "Y") {
    & (Join-Path $ScriptsDir "catfish-search.exe") daemon install
    Ok "装好。查状态：catfish-search daemon status"
} else {
    Ok "跳过。以后可手动：catfish-search daemon install"
}

# ---------- 收尾 ----------

Write-Host ""
Write-Host "=== 装好了 ===" -ForegroundColor Green
Write-Host @"

日常使用：

  catfish-search query "合同"
  catfish-search status
  catfish-search config

在 Hermes 里直接问"帮我找 X 的文档"，小鲶会自动调本地搜索。

如果 PowerShell 里 catfish-search 找不到，开一个新 PowerShell 窗口再试（让 Path 变更生效）。
"@
