<#
.SYNOPSIS
    鲶鱼中央服务 · Windows 一键部署

.DESCRIPTION
    deploy.sh 的 Windows 版。做的事一模一样, 六步:

      1. 体检宿主机 (Docker Desktop 装了没 / 在跑没 / 端口空不空 / 资源够不够)
      2. 体检 .env (必填字段全填了 + 密码不是占位)
      2.5 自动生成纯随机的密钥 —— **已有的绝不覆盖**
      3. docker compose build + up -d
      4. 等所有 service 健康 (有超时, 防卡死)
      5. smoke test (真打 healthcheck)
      6. 汇报状态 + 给客户 IT 的 URL 清单

    核心部署逻辑是 docker compose, 那部分本来就跨平台 —— 这个脚本只是把
    deploy.sh 那层外壳翻译过来, 顺便处理三件 Windows 特有的事:

      · 换行符    写 .env 强制用 LF (见下方 Write-EnvFile 的注释)
      · 文件权限  chmod 600 → icacls 去继承 + 只留当前用户
      · 环境探测  多查一道 Docker Desktop 引擎在不在跑 (装了 ≠ 起了)

.PARAMETER Command
    deploy  部署 (默认)
    status  看 service 状态
    logs    tail 日志, 后面可跟服务名 (默认 gateway)
    down    关 stack (保留 volume, 数据安全)
    nuke    删 stack + 所有 volume (危险, 会删 PG 数据)

.EXAMPLE
    # 第一次部署
    Copy-Item .env.production.example .env
    notepad .env          # 只填客户自己的: 内网端点 / INTERNAL_LLM_KEY / OIDC
    .\deploy.ps1          # 主密钥、PG 密码、HUB token 由脚本生成

.EXAMPLE
    .\deploy.ps1 status
    .\deploy.ps1 logs gateway
    .\deploy.ps1 down

.NOTES
    如果提示"禁止运行脚本", 用这条绕过 (只对本次会话生效, 不改系统设置):
        powershell -ExecutionPolicy Bypass -File .\deploy.ps1
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('deploy', 'status', 'logs', 'down', 'nuke')]
    [string]$Command = 'deploy',

    [Parameter(Position = 1)]
    [string]$Service = 'gateway'
)

$ErrorActionPreference = 'Stop'

# ── 输出 (客户 IT 要能一眼看出哪步挂了) ──────────────────────────
function Write-Err  { param([string]$m) Write-Host "[错误] $m" -ForegroundColor Red }
function Write-Ok   { param([string]$m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn2{ param([string]$m) Write-Host "[注意] $m" -ForegroundColor Yellow }
function Write-Info { param([string]$m) Write-Host "[信息] $m" -ForegroundColor Cyan }
function Write-Step {
    param([string]$m)
    Write-Host ""
    Write-Host "══ $m ══" -ForegroundColor White -BackgroundColor DarkBlue
}

# 失败时统一出口: 打一句"接下来做什么", 别让客户 IT 对着报错发呆
function Stop-WithHint {
    param([string]$Problem, [string[]]$Hints)
    Write-Err $Problem
    if ($Hints) {
        Write-Host ""
        Write-Host "  怎么办:" -ForegroundColor Yellow
        foreach ($h in $Hints) { Write-Host "    $h" }
    }
    Write-Host ""
    exit 1
}

# ── cd 到脚本所在目录 (能在任何地方调用) ──
Set-Location -Path $PSScriptRoot
$ComposeFile = 'docker-compose.yml'
$EnvFile     = '.env'

# ═══════════════════════════════════════════════════════════════
#  .env 读写
# ═══════════════════════════════════════════════════════════════
#
# ⚠ 写 .env 必须用 LF, 不能用 PowerShell 默认的 CRLF。
#
# docker compose 解析 .env 时不会 strip 行尾的 \r, 于是
#     PG_PASSWORD=abc123
# 会变成值 "abc123`r"。表现是 postgres 报密码错误 —— 而你把 .env 打开
# 一看密码完全正确, 复制出来比对也一模一样。这是本脚本里最值得小心的
# 一处, Set-Content / Out-File / >> 全都会写 CRLF, 所以统一走
# [IO.File]::WriteAllText + "`n" 拼接。
#
# 同理不能用 Add-Content 追加。

function Read-EnvLines {
    if (-not (Test-Path $EnvFile)) { return @() }
    # -Raw 读进来再自己切, 避免 Get-Content 按平台行尾切割的差异
    $raw = [IO.File]::ReadAllText((Resolve-Path $EnvFile))
    return ($raw -split "`r?`n")
}

function Write-EnvLines {
    param([string[]]$Lines)
    # 去掉末尾多余空行, 统一补一个结尾换行
    while ($Lines.Count -gt 0 -and $Lines[-1] -eq '') {
        $Lines = $Lines[0..($Lines.Count - 2)]
    }
    $text = ($Lines -join "`n") + "`n"
    [IO.File]::WriteAllText((Join-Path (Get-Location) $EnvFile), $text, (New-Object Text.UTF8Encoding($false)))
}

function Get-EnvValue {
    param([string]$Key)
    foreach ($line in Read-EnvLines) {
        if ($line -match "^$([regex]::Escape($Key))=(.*)$") {
            return $Matches[1]
        }
    }
    return $null
}

# 原地改, 不追加重复行 (对应 deploy.sh 的 set_env_var)
function Set-EnvValue {
    param([string]$Key, [string]$Value)
    $lines = @(Read-EnvLines)
    $done  = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($Key))=") {
            $lines[$i] = "$Key=$Value"
            $done = $true
            break
        }
    }
    if (-not $done) { $lines += "$Key=$Value" }
    Write-EnvLines -Lines $lines
}

# 当前值是不是"还没填"(空 / CHANGE_ME 开头) —— 对应 env_needs_value
function Test-EnvNeedsValue {
    param([string]$Key)
    $v = Get-EnvValue -Key $Key
    return ([string]::IsNullOrEmpty($v) -or $v.StartsWith('CHANGE_ME'))
}

# ═══════════════════════════════════════════════════════════════
#  随机密钥
# ═══════════════════════════════════════════════════════════════
#
# deploy.sh 用 openssl (装了 docker 的 Linux 机器上都有), Windows 上没有,
# 换成 .NET 的密码学随机源 —— 跟 openssl rand 同级, 不是 Get-Random
# (那个是伪随机, 不能拿来当密钥)。

function New-RandomBytes {
    param([int]$Count)
    $bytes = New-Object byte[] $Count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try   { $rng.GetBytes($bytes) }
    finally { $rng.Dispose() }
    return $bytes
}

# Fernet 密钥 = 32 字节随机的 url-safe base64 (+/ 换成 -_)
function New-FernetKey {
    return [Convert]::ToBase64String((New-RandomBytes -Count 32)).Replace('+', '-').Replace('/', '_')
}

function New-HexToken {
    param([int]$Bytes = 32)
    return (New-RandomBytes -Count $Bytes | ForEach-Object { $_.ToString('x2') }) -join ''
}

# PG 密码只用字母数字: 它要进 CATFISH_DB_URL 这个 URL
# (postgresql://user:PASS@host/db), 带 @ : / 的话要转义, 不值当。
# 32 位字母数字 ≈ 165 bit, 比"16 位含符号"强得多。
function New-PgPassword {
    $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    $bytes = New-RandomBytes -Count 32
    $sb = New-Object Text.StringBuilder
    foreach ($b in $bytes) { [void]$sb.Append($chars[$b % $chars.Length]) }
    return $sb.ToString()
}

# ═══════════════════════════════════════════════════════════════
#  子命令 (deploy 以外的直接处理完退出)
# ═══════════════════════════════════════════════════════════════
switch ($Command) {
    'status' {
        docker compose -f $ComposeFile ps
        exit $LASTEXITCODE
    }
    'logs' {
        docker compose -f $ComposeFile logs --tail=100 -f $Service
        exit $LASTEXITCODE
    }
    'down' {
        Write-Warn2 "关 stack (保留 volume, PG 数据安全)"
        docker compose -f $ComposeFile down
        Write-Ok "stack 已关。数据 volume (pgdata/gateway_data/hubdata) 保留, 下次部署自动接续。"
        exit 0
    }
    'nuke' {
        Write-Host ""
        Write-Warn2 "危险: 这会删掉所有 volume (PG 数据 + audit log + facts), 不可恢复"
        $confirm = Read-Host "确认要 nuke? 输 'nuke' 继续"
        if ($confirm -ne 'nuke') {
            Write-Info "已取消"
            exit 0
        }
        docker compose -f $ComposeFile down -v
        Write-Ok "stack + volume 全删。下次跑 deploy.ps1 从空开始。"
        exit 0
    }
}

# ═══════════════════════════════════════════════════════════════
#  Step 1/6  宿主机体检
# ═══════════════════════════════════════════════════════════════
Write-Step "1/6  宿主机体检"

# 1.1 docker 命令在不在
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Stop-WithHint "没找到 docker 命令 —— Docker Desktop 没装, 或者装完没重开终端。" @(
        "1. 装 Docker Desktop for Windows: https://docs.docker.com/desktop/install/windows-install/",
        "2. 装的时候勾上 'Use WSL 2 based engine'",
        "3. 装完**重开一个 PowerShell 窗口**再跑本脚本 (PATH 要重新加载)"
    )
}
Write-Ok "docker: $((docker --version) -join '')"

# 1.2 Docker 引擎在不在跑
#     Windows 上"装了"和"起了"是两回事 —— Docker Desktop 没启动的话,
#     docker 命令存在但所有调用都会挂在这里。Linux 上 systemd 起 daemon,
#     没这个问题, 所以 deploy.sh 里没这一步。
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker info 非 0" }
} catch {
    Stop-WithHint "Docker 引擎没在跑 (命令有, 但连不上 daemon)。" @(
        "1. 从开始菜单启动 Docker Desktop",
        "2. 等右下角托盘图标变成稳定的鲸鱼 (不再转圈), 通常 30-60 秒",
        "3. 再跑一次本脚本",
        "",
        "如果 Docker Desktop 起不来, 多半是 WSL2 没装或虚拟化没开:",
        "  wsl --install          # 装 WSL2 (要重启)",
        "  然后 BIOS 里确认虚拟化 (Intel VT-x / AMD-V) 是开的"
    )
}
Write-Ok "Docker 引擎在跑"

# 1.3 compose v2
docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-WithHint "docker compose v2 不可用 (老的 docker-compose.exe v1 不行, 必须是 plugin 形式)。" @(
        "Docker Desktop 4.x 自带 compose v2。如果这里报错, 多半是装了很老的版本 ——",
        "去 https://docs.docker.com/desktop/release-notes/ 升级到最新版。"
    )
}
Write-Ok "docker compose: $((docker compose version --short) -join '')"

# 1.4 端口冲突
#     deploy.sh 用 ss -ltn, Windows 上用 Get-NetTCPConnection。
#     web :80 是容器内端口, 不绑宿主机, 但 nginx 绑 80/443 所以仍要查。
$portMap = [ordered]@{
    80   = 'nginx http'
    443  = 'nginx https'
    8994 = 'wiki-hub'
    8996 = 'mcp-registry'
    8997 = 'skills-hub'
    8998 = 'identity'
    8999 = 'gateway'
}
$busy = @()
foreach ($p in $portMap.Keys) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $procName = try {
            (Get-Process -Id ($conn | Select-Object -First 1).OwningProcess -ErrorAction Stop).ProcessName
        } catch { '未知进程' }
        $busy += "端口 $p ($($portMap[$p])) 被 $procName 占用"
    }
}
if ($busy.Count -gt 0) {
    Stop-WithHint "端口冲突, 这些端口得先腾出来:" (
        $busy + @(
            "",
            "查占用: Get-NetTCPConnection -LocalPort 8999 -State Listen | " +
            "Select-Object OwningProcess",
            "如果是 IIS 占了 80: Stop-Service W3SVC",
            "或者改 docker-compose.yml 里的 ports 映射"
        )
    )
}
Write-Ok "端口 80 / 443 / 8994 / 8996 / 8997 / 8998 / 8999 全空"

# 1.5 资源 (至少 4 vCPU + 4GB RAM, BL-F10 实测最低)
$os = Get-CimInstance Win32_OperatingSystem
$totalMemMb = [math]::Round($os.TotalVisibleMemorySize / 1KB)
$cpuCount   = [int]$env:NUMBER_OF_PROCESSORS

if ($totalMemMb -gt 0 -and $totalMemMb -lt 4096) {
    Write-Warn2 "总内存 ${totalMemMb}MB < 4GB。BL-F10 实测 1000 员工峰值 ~1GB, 算上 PG+identity+nginx 推荐 >= 4GB"
}
if ($cpuCount -gt 0 -and $cpuCount -lt 4) {
    Write-Warn2 "vCPU 只有 $cpuCount 个 (推荐 >= 4)。gateway 默认 4 worker, 少于 4 核会过分配。"
    Write-Warn2 "  改 .env: GATEWAY_WORKERS=$cpuCount 和 IDENTITY_WORKERS=1"
}
Write-Ok "宿主机: $cpuCount vCPU / ${totalMemMb}MB 内存"

# 1.6 Docker Desktop 分给容器的内存 (Windows 特有)
#     宿主机 16G 不代表容器能用 16G —— WSL2 后端有自己的上限, 默认是宿主机的
#     50% 或 8GB 取小。这一项 Linux 上不存在, 但在 Windows 上是真会卡住部署的:
#     PG + 5 个 Python 服务 + nginx 挤在 2G 里会被 OOM killer 轮流杀。
try {
    $dockerMemBytes = (docker info --format '{{.MemTotal}}' 2>$null)
    if ($dockerMemBytes -match '^\d+$') {
        $dockerMemMb = [math]::Round([int64]$dockerMemBytes / 1MB)
        if ($dockerMemMb -lt 4096) {
            Write-Warn2 "Docker 可用内存只有 ${dockerMemMb}MB (< 4GB)。"
            Write-Warn2 "  Docker Desktop → Settings → Resources → Memory 调到 >= 4GB;"
            Write-Warn2 "  WSL2 后端的话改 %USERPROFILE%\.wslconfig 里的 memory= 再 wsl --shutdown"
        } else {
            Write-Ok "Docker 可用内存: ${dockerMemMb}MB"
        }
    }
} catch {
    # 拿不到就算了, 不阻塞部署
}

# ═══════════════════════════════════════════════════════════════
#  Step 2/6  .env 体检
# ═══════════════════════════════════════════════════════════════
Write-Step "2/6  .env 体检"

if (-not (Test-Path $EnvFile)) {
    Stop-WithHint ".env 不存在。" @(
        "先复制模板再改:",
        "  Copy-Item .env.production.example .env",
        "  notepad .env",
        "",
        "只需要填客户自己的那几项 (内网 LLM 端点 / INTERNAL_LLM_KEY / OIDC / DOMAIN),",
        "主密钥、PG 密码、SKILLS_HUB_TOKEN 由本脚本自动生成, 不用自己想。"
    )
}
Write-Ok ".env 存在"

# .env 里有没有混进 CRLF —— 上面 Write-EnvFile 那段注释解释了为什么致命。
# 用记事本编辑过的 .env 一定是 CRLF, 而客户 IT 十有八九就是用记事本。
$envRaw = [IO.File]::ReadAllText((Resolve-Path $EnvFile))
if ($envRaw.Contains("`r`n")) {
    Write-Warn2 ".env 是 CRLF 换行 (多半被记事本/写字板保存过)。"
    Write-Warn2 "  docker compose 读它时值末尾会多一个不可见的 \r ——"
    Write-Warn2 "  比如密码会变成 'abc123\r', 表现是 PG 报密码错误但你看着完全正确。"
    Write-Info  "  正在自动转成 LF..."
    Write-EnvLines -Lines ($envRaw -split "`r?`n")
    Write-Ok ".env 已转成 LF"
}

# ═══════════════════════════════════════════════════════════════
#  Step 2.5/6  密钥自动生成 (只填空的, 已有的一个字不改)
# ═══════════════════════════════════════════════════════════════
Write-Step "2.5/6  密钥自动生成 (只填空的, 已有的一个字不改)"

$generated = @()

# ── 主密钥 ──
# ⚠⚠ 最重要的一条: 已经有合法值就绝对不碰。
#    覆盖 CATFISH_SECRET_KEY = 所有存库的供应商 API key 全部解不开, 只能逐个
#    去各家后台重新申请。这个脚本会被重跑 (改配置、升级、排障), 所以
#    "重跑安全"不是锦上添花, 是硬要求。
if (Test-EnvNeedsValue -Key 'CATFISH_SECRET_KEY') {
    Set-EnvValue -Key 'CATFISH_SECRET_KEY' -Value (New-FernetKey)
    $generated += 'CATFISH_SECRET_KEY'
    Write-Ok "已生成 CATFISH_SECRET_KEY (供应商 API key 的加密主密钥)"
} else {
    Write-Info "CATFISH_SECRET_KEY 已有值, 不动 (改了会让所有存库的 API key 解不开)"
}

# ── Skills Hub token ──
if (Test-EnvNeedsValue -Key 'SKILLS_HUB_TOKEN') {
    Set-EnvValue -Key 'SKILLS_HUB_TOKEN' -Value (New-HexToken)
    $generated += 'SKILLS_HUB_TOKEN'
    Write-Ok "已生成 SKILLS_HUB_TOKEN (manager 发布 skill 用)"
} else {
    Write-Info "SKILLS_HUB_TOKEN 已有值, 不动"
}

# ── PG 密码 ──
# ⚠ 只在**首次部署**生成。postgres 的密码是 volume 初始化那一刻写进去的,
#   之后改 .env 不会改库里的密码, 只会导致连不上。所以 volume 已经存在时,
#   哪怕 .env 里还是占位符也不生成 —— 那种情况得人工处理, 不能让脚本把一个
#   能用的库搞成连不上。
$pgVolume = "$((Get-Item -Path '.').Name)_pgdata"
if (Test-EnvNeedsValue -Key 'PG_PASSWORD') {
    docker volume inspect $pgVolume 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Stop-WithHint "PG_PASSWORD 还是占位, 但 postgres 数据卷 ($pgVolume) 已经存在了。" @(
            "库里的密码是第一次启动时定下的, 现在生成一个新的只会连不上。",
            "",
            "二选一:",
            "  A. 把原来那个密码填回 .env 的 PG_PASSWORD",
            "  B. .\deploy.ps1 nuke   然后重来 (会删掉库里所有数据)"
        )
    }
    Set-EnvValue -Key 'PG_PASSWORD' -Value (New-PgPassword)
    $generated += 'PG_PASSWORD'
    Write-Ok "已生成 PG_PASSWORD (首次部署)"
} else {
    Write-Info "PG_PASSWORD 已有值, 不动"
}

if ($generated.Count -gt 0) {
    Write-Host ""
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Yellow
    Write-Host "  刚生成了 $($generated.Count) 个密钥, 已写进 .env" -ForegroundColor Yellow
    Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Yellow
    foreach ($k in $generated) {
        Write-Host "    $k=$(Get-EnvValue -Key $k)"
    }
    Write-Host ""
    if ($generated -contains 'CATFISH_SECRET_KEY') {
        Write-Host "  ⚠ CATFISH_SECRET_KEY 现在就存进公司密码管理器。" -ForegroundColor Red
        Write-Host "    它丢了的话, 所有存在数据库里的供应商 API key 都解不开 ——"
        Write-Host "    只能逐个去各家后台重新申请、再在界面上重填一遍。代码兜不住。"
        Write-Host "    (备份 .env 本身也算, 但别只依赖服务器上那一份。)"
        Write-Host ""
    }
}

# .env 里有明文密码, 别让同机的其他用户读到。
# Linux 的 chmod 600 在 Windows 上对应: 关掉继承 + 只保留当前用户完全控制。
try {
    icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(F)" 2>&1 | Out-Null
    Write-Ok ".env 权限已收紧 (只有 $($env:USERNAME) 能读)"
} catch {
    Write-Warn2 ".env 权限收紧失败 (不影响部署, 但同机其他用户能读到密码)"
}

# ── 必填 + 不能是占位 ──
# ⚠ CATFISH_SECRET_KEY 也在这里 —— 上面那步会自动生成, 这里是兜底。
#   8/1 之前它不在, 于是客户忘了改的话部署会"成功", 但供应商页面上永远存不了
#   key。部署脚本说 OK 而功能是坏的, 正是最难查的那种。
$required = [ordered]@{
    'PG_PASSWORD'          = 'CHANGE_ME_TO_STRONG_PASSWORD'
    'CATFISH_OIDC_ISSUER'  = 'CHANGE_ME_OIDC_ISSUER_URL'
    'SKILLS_HUB_TOKEN'     = 'CHANGE_ME_RANDOM_32_CHARS'
    'INTERNAL_LLM_KEY'     = 'CHANGE_ME'
    'CATFISH_SECRET_KEY'   = 'CHANGE_ME_RUN_THE_COMMAND_ABOVE'
}
$missing = @()
foreach ($key in $required.Keys) {
    $val = Get-EnvValue -Key $key
    if ([string]::IsNullOrEmpty($val) -or $val -eq $required[$key]) {
        $missing += $key
    }
}
if ($missing.Count -gt 0) {
    Stop-WithHint ".env 里这些字段还是占位 / 空, 必须改成真值:" (
        ($missing | ForEach-Object { "  - $_" }) + @(
            "",
            "notepad .env    # 改完存盘再跑一次本脚本",
            "",
            "其中 INTERNAL_LLM_KEY 和内网端点只有客户自己知道, 得问对方 IT。"
        )
    )
}
Write-Ok ".env 必填字段全填了 (主密钥/PG密码/OIDC/SKILLS_HUB_TOKEN/INTERNAL_LLM_KEY)"

$pgPass = Get-EnvValue -Key 'PG_PASSWORD'
if ($pgPass -and $pgPass.Length -lt 16) {
    Write-Warn2 "PG_PASSWORD 只有 $($pgPass.Length) 位, 推荐 >= 16 位"
}

# ═══════════════════════════════════════════════════════════════
#  Step 3/6  build + up -d
# ═══════════════════════════════════════════════════════════════
Write-Step "3/6  build + up -d"

Write-Info "build (首次约 3-10 分钟, Windows 上比 Linux 慢一些; 之后有缓存 < 30 秒)..."
docker compose -f $ComposeFile build --pull
if ($LASTEXITCODE -ne 0) {
    Stop-WithHint "docker compose build 失败。" @(
        "常见原因:",
        "  · 拉不到基础镜像 —— 内网环境要先配 Docker 镜像加速 / 私有 registry",
        "  · 磁盘满了 —— docker system df 看占用, docker system prune 清理",
        "  · 换行符问题 —— 如果报 'bad interpreter' 或 entrypoint 找不到,",
        "    是 .sh 被 git 转成了 CRLF。仓库根目录已有 .gitattributes 防这个,",
        "    但如果是在加它之前 clone 的, 需要重新 checkout:",
        "        git rm --cached -r . ; git reset --hard"
    )
}

Write-Info "up -d (依次起 pg → identity → gateway → mcp-registry → skills-hub → web → nginx)..."
docker compose -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) {
    Stop-WithHint "docker compose up 失败。" @(
        "看哪个服务起不来:  .\deploy.ps1 status",
        "看具体报错:        .\deploy.ps1 logs <服务名>"
    )
}
Write-Ok "stack 已起, 进入健康检查"

# ═══════════════════════════════════════════════════════════════
#  Step 4/6  等 healthy
# ═══════════════════════════════════════════════════════════════
Write-Step "4/6  等所有 service healthy (每个最多等 120 秒)"

function Wait-Healthy {
    param([string]$Svc, [int]$TimeoutSec = 120)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $status = docker inspect --format='{{.State.Health.Status}}' "catfish-$Svc" 2>$null
        if ($LASTEXITCODE -ne 0) { $status = 'missing' }
        $status = "$status".Trim()

        switch ($status) {
            'healthy' {
                Write-Ok "${Svc}: healthy (${elapsed}秒)"
                return $true
            }
            'unhealthy' {
                Write-Err "${Svc}: unhealthy —— 看日志: .\deploy.ps1 logs $Svc"
                return $false
            }
            'missing' {
                # nginx 没配 healthcheck, 跳过
                if ($Svc -eq 'nginx') {
                    Write-Ok "${Svc}: (无 healthcheck, 跳过)"
                    return $true
                }
            }
        }
        Start-Sleep -Seconds 3
        $elapsed += 3
        if ($elapsed % 30 -eq 0) { Write-Info "  ${Svc}: 还在启动... (${elapsed}秒)" }
    }
    Write-Err "${Svc}: ${TimeoutSec} 秒内没变成 healthy —— 看日志: .\deploy.ps1 logs $Svc"
    return $false
}

$allHealthy = $true
foreach ($svc in @('postgres', 'identity', 'gateway', 'skills-hub', 'wiki-hub')) {
    if (-not (Wait-Healthy -Svc $svc -TimeoutSec 120)) { $allHealthy = $false }
}

if (-not $allHealthy) {
    Stop-WithHint "至少一个服务没起来。" @(
        ".\deploy.ps1 status           # 看每个服务什么状态",
        ".\deploy.ps1 logs gateway     # 看 gateway 报什么",
        "",
        "Windows 上最常见的两个原因:",
        "  · Docker 内存不够 (Settings → Resources → Memory 调到 >= 4GB)",
        "  · PG 密码跟已有数据卷对不上 (见上面 Step 2.5 的说明)"
    )
}

# ═══════════════════════════════════════════════════════════════
#  Step 5/6  smoke test
# ═══════════════════════════════════════════════════════════════
Write-Step "5/6  smoke test (真打 healthcheck 接口)"

function Test-Endpoint {
    param([string]$Url, [string]$Desc)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) {
            Write-Ok "${Desc}: $($r.StatusCode) OK"
            return $true
        }
        Write-Err "${Desc} ($Url): HTTP $($r.StatusCode)"
        return $false
    } catch {
        Write-Err "${Desc} ($Url): 连不上"
        return $false
    }
}

$smokeOk = $true
if (-not (Test-Endpoint 'http://127.0.0.1:8999/healthz' 'gateway /healthz')) { $smokeOk = $false }
if (-not (Test-Endpoint 'http://127.0.0.1:8998/.well-known/openid-configuration' 'identity OIDC discovery')) { $smokeOk = $false }
if (-not (Test-Endpoint 'http://127.0.0.1:8997/healthz' 'skills-hub /healthz')) { $smokeOk = $false }
if (-not (Test-Endpoint 'http://127.0.0.1:8996/health'  'mcp-registry /health')) { $smokeOk = $false }
if (-not (Test-Endpoint 'http://127.0.0.1:8994/healthz' 'wiki-hub /healthz')) { $smokeOk = $false }

# web SPA 走 nginx。部署当下可能还没配 SSL 证书, 所以 200/301 都算过。
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1/' -TimeoutSec 5 -UseBasicParsing `
                           -MaximumRedirection 0 -ErrorAction SilentlyContinue
    if ($r -and ($r.StatusCode -eq 200 -or $r.StatusCode -eq 301)) {
        Write-Ok "web SPA via nginx: $($r.StatusCode)"
    } else {
        Write-Warn2 "web SPA via nginx 验证不过 —— 可能 nginx.conf 还没改 server_name, 或证书没配"
    }
} catch {
    Write-Warn2 "web SPA via nginx 验证不过 —— 可能 nginx.conf 还没改 server_name, 或证书没配"
}

# gateway 多 worker 验证 (BL-F10 真根因 fix)
$workerLines = (docker compose -f $ComposeFile logs gateway 2>&1 | Select-String -Pattern 'Started server process').Count
$expectedWorkers = Get-EnvValue -Key 'GATEWAY_WORKERS'
if (-not $expectedWorkers) { $expectedWorkers = 4 }
if ([int]$workerLines -lt [int]$expectedWorkers) {
    Write-Warn2 "gateway 起了 $workerLines 个 worker (期望 $expectedWorkers)。检查 app.py 的 uvicorn.run(workers=...) 有没有真传。"
} else {
    Write-Ok "gateway uvicorn workers: $workerLines (BL-F10 多 worker fix 生效)"
}

if (-not $smokeOk) {
    Stop-WithHint "smoke test 有失败项。" @(
        ".\deploy.ps1 logs gateway     # 先看 gateway",
        ".\deploy.ps1 status           # 确认服务都还活着"
    )
}

# ═══════════════════════════════════════════════════════════════
#  Step 6/6  汇报
# ═══════════════════════════════════════════════════════════════
Write-Step "6/6  部署完成"

$domain = Get-EnvValue -Key 'DOMAIN'
if (-not $domain) { $domain = 'catfish.example.com' }

Write-Host ""
Write-Host "  ✓ 部署成功" -ForegroundColor Green
Write-Host ""
Write-Host "服务状态:" -ForegroundColor White
docker compose -f $ComposeFile ps --format "table {{.Service}}`t{{.Status}}`t{{.Ports}}"

Write-Host ""
Write-Host "本机自检 (在这台服务器上访问):" -ForegroundColor White
Write-Host "  gateway:     http://127.0.0.1:8999/healthz"
Write-Host "  identity:    http://127.0.0.1:8998/.well-known/openid-configuration"
Write-Host "  skills-hub:  http://127.0.0.1:8997/healthz"

Write-Host ""
Write-Host "员工访问 (Companion 里填的就是这个):" -ForegroundColor White
Write-Host "  https://$domain/         → gateway (对话/配额/审计)"
Write-Host "  https://$domain/sso/     → identity (SSO 登录)"
Write-Host "  https://$domain/hub/     → skills-hub (技能分发)"
Write-Host "  前提: nginx.conf 里配了 DOMAIN, 证书放在 .\certs\cert.pem + key.pem" -ForegroundColor Yellow

Write-Host ""
Write-Host "日常运维:" -ForegroundColor White
Write-Host "  .\deploy.ps1 status         # 看服务状态"
Write-Host "  .\deploy.ps1 logs gateway   # 看日志"
Write-Host "  .\deploy.ps1 down           # 停服务 (数据保留)"
Write-Host "  docker compose pull; .\deploy.ps1    # 升级"

Write-Host ""
Write-Host "下一步:" -ForegroundColor White
Write-Host "  1. 把 https://$domain 发给员工"
Write-Host "  2. Companion 里配这个地址 (catfish_central_url)"
Write-Host "  3. 找一个员工 SSO 登一次, 验证整条链路通"
Write-Host ""
