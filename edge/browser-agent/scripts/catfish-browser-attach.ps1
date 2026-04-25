# =============================================================
# catfish-browser-attach.ps1 (Windows)
# =============================================================
# 跟 catfish-browser-attach.sh 等价，Windows PowerShell 版。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\catfish-browser-attach.ps1
#
# 不需要管理员权限。

[CmdletBinding()]
param(
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"
$DebugUrl = "http://localhost:$Port/json/version"
$HermesConfig = Join-Path $env:USERPROFILE ".hermes\config.yaml"

# Chrome 2024 起的安全策略：默认 profile 下 --remote-debugging-port 被静默忽略，
# 防止恶意软件劫持员工登录态。必须用独立 profile 绕过。
$ChromeProfileDir = Join-Path $env:USERPROFILE ".catfish\chrome-profile"

function Step($n, $msg) { Write-Host "[$n/5] $msg" -ForegroundColor Yellow }
function Ok($msg)       { Write-Host "    OK $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    警告 $msg" -ForegroundColor DarkYellow }
function Err($msg)      { Write-Host "    错误 $msg" -ForegroundColor Red }

function Find-ChromeExe {
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Chromium\Application\chrome.exe"
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

function Test-DebugPort {
    try {
        $r = Invoke-WebRequest -Uri $DebugUrl -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

# ---------- 1. 看端口是否已通 ----------

Step 1 "检查 Chrome 是否已在调试模式运行"
$ChromeUp = Test-DebugPort
if ($ChromeUp) {
    Ok "Chrome 已在 :$Port 开调试端口，跳过启动"
} else {
    Ok "9222 未开，准备启动 Chrome"
}

# ---------- 2. 启动 Chrome ----------

Step 2 "启动 Chrome（带调试端口）"
if (-not $ChromeUp) {
    $ChromeExe = Find-ChromeExe
    if (-not $ChromeExe) {
        Err "没找到 Chrome / Chromium。先装一个再来。"
        exit 1
    }

    # 用独立 profile 目录，跟员工日常 Chrome 并存不冲突
    New-Item -ItemType Directory -Force -Path $ChromeProfileDir | Out-Null

    Write-Host "    启动命令：`"$ChromeExe`" --remote-debugging-port=$Port --user-data-dir=`"$ChromeProfileDir`""
    Start-Process -FilePath $ChromeExe -ArgumentList @(
        "--remote-debugging-port=$Port",
        "--user-data-dir=$ChromeProfileDir",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session"
    ) | Out-Null

    # 等待 Chrome 就绪（最多 15 秒）
    for ($i = 1; $i -le 30; $i++) {
        if (Test-DebugPort) {
            Ok "Chrome 就绪（$($i * 500)ms）"
            break
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not (Test-DebugPort)) {
        Err "Chrome 启动了但 15 秒内 9222 没通。检查端口占用。"
        exit 3
    }
} else {
    Ok "跳过启动"
}

# ---------- 3. 抓 webSocketDebuggerUrl ----------

Step 3 "抓 CDP WebSocket URL"
try {
    $json = Invoke-RestMethod -Uri $DebugUrl -TimeoutSec 5
    $WsUrl = $json.webSocketDebuggerUrl
} catch {
    Err "从 $DebugUrl 拿数据失败：$_"
    exit 4
}
if (-not $WsUrl) {
    Err "响应里没有 webSocketDebuggerUrl 字段"
    exit 4
}
Ok "$WsUrl"

# ---------- 4. 写进 ~/.hermes/config.yaml ----------

Step 4 "写入 $HermesConfig 的 browser.cdp_url"
$configDir = Split-Path -Parent $HermesConfig
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
if (-not (Test-Path $HermesConfig)) { New-Item -ItemType File -Path $HermesConfig | Out-Null }

# 用 Python + pyyaml 写（PowerShell 没现成的 yaml 模块，复用员工的 catfish venv）
$catfishVenv = Join-Path $env:USERPROFILE ".catfish\venv\Scripts\python.exe"
$pythonCmd = if (Test-Path $catfishVenv) { $catfishVenv } else { "python" }

$regScript = @"
import sys, yaml
from pathlib import Path
cfg = Path(sys.argv[1])
ws = sys.argv[2]
text = cfg.read_text(encoding='utf-8') if cfg.exists() else ''
data = yaml.safe_load(text) if text.strip() else {}
if not isinstance(data, dict): data = {}
browser = data.get('browser') or {}
if not isinstance(browser, dict): browser = {}
old = browser.get('cdp_url')
browser['cdp_url'] = ws
data['browser'] = browser
cfg.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding='utf-8')
print('updated' if old else 'added', 'browser.cdp_url')
"@
$tmp = [IO.Path]::GetTempFileName() + ".py"
$regScript | Set-Content -Path $tmp -Encoding utf8
& $pythonCmd $tmp $HermesConfig $WsUrl
Remove-Item $tmp

# ---------- 5. 总结 ----------

Step 5 "完成"
Write-Host @"

    ✓ 专用 Chrome 实例已启，profile 目录：$ChromeProfileDir
    ✓ Hermes 下次 browser_navigate 从 30~60 秒变 <3 秒
    ✓ 和你日常 Chrome 完全隔离（两个 Chrome 同时在任务栏）

    隐私模型：
      · 专用 Chrome profile，Hermes 只能看到这里面
      · 你日常 Chrome 的东西 Hermes 看不到
      · 要 Hermes 访问公司内网，在这个专用 Chrome 里登一次即可，登录态自动持久化
      · 这比"共用日常 Chrome"更安全

    首次使用：在这个专用 Chrome 里打开 Jira / Confluence 登一下

    验证：hermes -> "帮我打开 github.com 看首页" 应该 <3 秒

    卸载：
      bash catfish-browser-detach.sh
      删 profile：Remove-Item -Recurse -Force "$ChromeProfileDir"
"@
