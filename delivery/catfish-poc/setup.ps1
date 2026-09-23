<#
.SYNOPSIS
    Catfish 中央服务一键装机 —— Windows 原生

.DESCRIPTION
    setup.sh 的 Windows 版, 步骤和判据完全一致。

    ⚠ 这**不是** central/deploy.ps1。那个脚本跑 `docker compose build --pull`,
      在客户机器上必然失败 —— 交付包里只有镜像和配置, 没有源码, 而且 --pull
      会去 docker.io 拉 postgres/nginx, 内网直接挂。deploy.ps1 是给**我们自己
      有源码的机器**用的, 从来不该进交付包。

    ── 两份实现怎么保证不跑偏 ──────────────────────────────────

    没有"两份实现"。真正容易出错的三段逻辑都在 tools/ 下的 Python 里, 这个
    脚本和 setup.sh 调的是同一个文件:

        tools/envgen.py    生成/升级 .env (密钥保留、备份、派生配置)
        tools/seedgen.py   identity 的 users.yaml / clients.yaml
        tools/certgen.py   自签证书体系 (CA 十年 + 服务器证书 397 天)

    它们跑在 catfish-identity 镜像里 —— Docker 本来就是硬前提, 所以宿主机
    不需要 openssl / python / sed。这正是 Windows 装不了的老原因:
    setup.sh 靠 openssl 和 /dev/urandom, Windows 两个都没有。

    这个脚本自己只干平台相关的事: 探 IP、load 镜像、起 compose、验健康。

.PARAMETER ServerIp
    服务器 IP。不传则自动探测并让你确认。

.PARAMETER EnableHttps
    1 = web 容器额外监听 443 (默认) · 0 = 只跑 HTTP。
    ⚠ 裸 IP + HTTP 时员工浏览器会**卡在"加载中…"且不报错** —— 前端 PKCE
      要 crypto.subtle, 浏览器只在安全上下文提供它。所以默认 HTTPS。

.PARAMETER Upgrade
    1 = 升级 (保留现有 .env / 数据 / 证书, 不从 .env.example 覆盖)

.PARAMETER ImageTar
    镜像 tar 路径。不传则自动找 images\*.tar.gz。

.PARAMETER RegenEnvOnly
    1 = 只重生成 .env 就退出, 不碰 docker。改 IP 时用。

.EXAMPLE
    # 首次装机 (自动探 IP, 默认 HTTPS)
    powershell -ExecutionPolicy Bypass -File .\setup.ps1

.EXAMPLE
    # 指定 IP
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -ServerIp 192.168.100.50

.EXAMPLE
    # 升级
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Upgrade 1 `
        -ImageTar .\images\catfish-poc-central-amd64-20260922.tar.gz

.NOTES
    前置: Docker Desktop (WSL2 后端) 装好**并且引擎在跑** —— 装了 ≠ 起了。
    提示"禁止运行脚本"时用: powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>
[CmdletBinding()]
param(
    [string]$ServerIp = $env:SERVER_IP,
    [ValidateSet('', '0', '1')][string]$EnableHttps = $env:ENABLE_HTTPS,
    [ValidateSet('0', '1')][string]$Upgrade = $(if ($env:UPGRADE) { $env:UPGRADE } else { '0' }),
    [string]$ImageTar = $env:IMAGE_TAR,
    [ValidateSet('0', '1')][string]$RegenEnvOnly = $(if ($env:REGEN_ENV_ONLY) { $env:REGEN_ENV_ONLY } else { '0' }),
    [string]$HttpsPort = $env:CATFISH_HTTPS_PORT,
    [string]$GatewayWorkers = $env:GATEWAY_WORKERS,
    [string]$IdentityWorkers = $env:IDENTITY_WORKERS,
    [string]$AdminPassword = $(if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { 'catfish_2026' }),
    [ValidateSet('0', '1')][string]$SkipImageLoad = $(if ($env:SKIP_IMAGE_LOAD) { $env:SKIP_IMAGE_LOAD } else { '0' })
)

$ErrorActionPreference = 'Stop'
# PowerShell 默认把 stderr 当错误。docker 往 stderr 打进度是正常的, 不拦。
$PSNativeCommandUseErrorActionPreference = $false

function Say     { param($m) Write-Host $m }
function SayOk   { param($m) Write-Host "  ✓ $m" -ForegroundColor Green }
function SayWarn { param($m) Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Die {
    param($m, [string[]]$hints = @())
    Write-Host ""
    Write-Host "❌ $m" -ForegroundColor Red
    foreach ($h in $hints) { Write-Host "   $h" }
    Write-Host ""
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# docker -v 的宿主侧路径统一用正斜杠。`C:\dir:/work` 里前面那个冒号是盘符,
# 后面那个是分隔符, docker 的解析器对反斜杠形式**在有空格的路径上会出错**。
# 正斜杠 Docker Desktop 一样认, 而且没有这个歧义。
$MountDir = $ScriptDir -replace '\\', '/'

Say "═══════════════════════════════════════════════════════"
Say "  Catfish 中央服务一键装机 (Windows)"
Say "═══════════════════════════════════════════════════════"

# ── 0. 宿主机体检 ─────────────────────────────────────────
# 装了 Docker Desktop ≠ 引擎在跑。分开报, 否则后面每条 docker 命令都会以
# 一种看不懂的方式失败。
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Die "找不到 docker 命令" @(
        "装 Docker Desktop for Windows (需要 WSL2 后端):",
        "  https://www.docker.com/products/docker-desktop/",
        "装完重开一个 PowerShell 窗口再跑 (PATH 要刷新)。"
    )
}
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Die "Docker 引擎没在跑" @(
        "开始菜单启动 Docker Desktop, 等托盘图标变成稳定状态 (不再转) 再重跑。",
        "装了但没启动是最常见的一种 —— docker 命令在, 但连不上引擎。"
    )
}
docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Die "docker compose v2 不可用" @(
        "必须是 plugin 形式的 compose v2 (Docker Desktop 4.x 自带),",
        "老的独立 docker-compose.exe v1 不行。"
    )
}
SayOk "docker + compose 就绪"

# ── 镜像 tag 的唯一来源 = docker-compose.yml ──────────────
# 跟 setup.sh 同一条规则: tag 只写在 compose 里, 脚本从那儿读。
# 三边 (compose / setup.sh / setup.ps1 / build-package.sh) 只剩一个源头。
$ComposePath = Join-Path $ScriptDir 'docker-compose.yml'
if (-not (Test-Path $ComposePath)) { Die "找不到 docker-compose.yml · 请在装机目录里跑" }

$ComposeImages = Select-String -Path $ComposePath -Pattern '^\s+image:\s+(\S+)' |
    ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -Unique
$IdentityImage = $ComposeImages | Where-Object { $_ -like 'catfish-identity:*' } | Select-Object -First 1
if (-not $IdentityImage) { Die "docker-compose.yml 里找不到 catfish-identity 的 image 行" }

# ── 参数解析 ──────────────────────────────────────────────
$EnvPath = Join-Path $ScriptDir '.env'

if ($Upgrade -eq '1' -and -not (Test-Path $EnvPath)) {
    Die "Upgrade 1 但当前目录没有 .env · 拒绝按新装流程生成配置" @(
        "请回到原安装目录, 或确认旧 .env 已备份后再升级。"
    )
}

function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path $EnvPath)) { return '' }
    $m = Select-String -Path $EnvPath -Pattern "^$Key=(.*)$" | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value.Trim() }
    return ''
}

# 升级时未显式指定就沿用现场的模式, 避免 HTTPS 部署被无意切回 HTTP。
if (-not $EnableHttps) { $EnableHttps = Get-EnvValue 'CATFISH_ENABLE_HTTPS' }
if (-not $EnableHttps) {
    if ($Upgrade -eq '1') {
        $certOk = (Test-Path "$ScriptDir\certs\cert.pem") -and (Test-Path "$ScriptDir\certs\key.pem")
        $EnableHttps = if ($certOk) { '1' } else { '0' }
    } else {
        $EnableHttps = '1'
    }
}
if (-not $HttpsPort) { $HttpsPort = Get-EnvValue 'CATFISH_HTTPS_PORT' }
if (-not $HttpsPort) { $HttpsPort = '443' }

# ── 1. 探测 / 确认 server IP ──────────────────────────────
if (-not $ServerIp) {
    # 排掉 loopback / APIPA / Docker 自己的 NAT 网段 —— 跟 setup.sh 同一组规则。
    # WSL 和 Hyper-V 的虚拟网卡在 Windows 上很常见, 选中它们的话员工连不上。
    $cand = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch '^(127\.|169\.254\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[0-1]\.)' -and
            $_.InterfaceAlias -notmatch 'Loopback|WSL|vEthernet|Hyper-V'
        } |
        Sort-Object -Property SkipAsSource, InterfaceMetric |
        Select-Object -ExpandProperty IPAddress

    if (-not $cand) {
        Die "无法自动探测 server IP" @("显式指定: .\setup.ps1 -ServerIp 192.168.x.x")
    }
    if ($cand -is [array] -and $cand.Count -gt 1) {
        Say "→ 探测到多个 IP, 选一个员工能访问到的:"
        for ($i = 0; $i -lt $cand.Count; $i++) { Say "    [$i] $($cand[$i])" }
        $pick = Read-Host "  序号 (回车 = 0)"
        if (-not $pick) { $pick = '0' }
        $ServerIp = $cand[[int]$pick]
    } else {
        $ServerIp = @($cand)[0]
        Say "→ 自动探测 server IP: $ServerIp"
        $confirm = Read-Host "  确认? (y/n · 回车 = y)"
        if ($confirm -and $confirm -notmatch '^[yY]$') {
            Die "请显式指定" @(".\setup.ps1 -ServerIp 192.168.x.x")
        }
    }
} else {
    Say "→ 使用指定 IP: $ServerIp"
}

# ── 2.pre · 先装 image, 再写任何配置 ──────────────────────
#
# 顺序跟 setup.sh 一致, 两个原因:
#  1. 缺镜像该在写任何配置**之前**就报 —— 否则现场看到的是"装到一半停了",
#     得先搞清楚哪些文件已经被改过才敢重来。
#  2. 镜像在位之后, 后面三个 tools/*.py 才能在容器里跑 —— 那是 Windows
#     不需要 openssl / python 的前提。
if ($RegenEnvOnly -eq '1') {
    Say "→ RegenEnvOnly 1 · 跳过 image load"
} else {
    if (-not $ImageTar) {
        $auto = Get-ChildItem -Path (Join-Path $ScriptDir 'images') -Filter '*.tar.gz' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($auto) {
            Say "→ 自动发现 image tar: $($auto.Name)"
            $ImageTar = $auto.FullName
        }
    }

    if ($ImageTar) {
        if (-not (Test-Path $ImageTar)) { Die "ImageTar 找不到: $ImageTar" }
        if ($SkipImageLoad -eq '1') {
            Say "→ SkipImageLoad 1 · 跳过 load"
            SayWarn "本地镜像可能不是包里那份 · 只在明确知道两者一致时才用这个开关"
        } else {
            # ── 无条件 load ──
            # 老逻辑是"tag 在就跳过", 而升级场景恰恰是"新 tar + 完全相同的 tag",
            # 于是跑的还是旧镜像, 却一路绿灯装完。实测撞过: 6 个 image ID 跟
            # 三小时前那批一模一样, 新构建的修复一个都没进去, verify 全绿。
            # load 本身幂等 (层已存在就秒过), 多等几分钟换"包里是什么就跑什么"。
            Say "→ load image tar (~5-15 min): $ImageTar"
            $before = docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>$null |
                Where-Object { $_ -like 'catfish*' } | Sort-Object

            # gzip 解压交给 docker load 自己做 —— 它认 gzip。
            # 注意用 -i 而不是管道: PowerShell 的管道是 UTF-16 文本流, 二进制
            # 走管道会被改写, docker load 会报 "unexpected EOF"。
            docker load -i $ImageTar
            if ($LASTEXITCODE -ne 0) { Die "docker load 失败 (详情见上)" }
            SayOk "装完"

            $after = docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>$null |
                Where-Object { $_ -like 'catfish*' } | Sort-Object
            if (($before -join "`n") -eq ($after -join "`n")) {
                Say "  · image ID 无变化 (本地原本就是包里这份)"
            } else {
                Say "  · image ID 有更新"
            }
        }
    }

    # verify: 缺镜像就 fail loud, 别让 docker compose 去 pull —— 内网拉不到,
    # 而那时的报错会指向网络, 不指向"tar 没放对地方"。
    $missing = @()
    foreach ($img in $ComposeImages) {
        docker image inspect $img 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $missing += $img }
    }
    if ($missing.Count -gt 0) {
        Die "本地缺 image: $($missing -join ' ')" @(
            "指定 ImageTar 或把 tar 放到 images\ 目录:",
            "  .\setup.ps1 -ImageTar .\images\catfish-poc-central-amd64-<date>.tar.gz",
            "(内网机连不到 docker.io · 必须本地 load)"
        )
    }
    SayOk "compose 需要的 image 都在本地"
}

# ── 共用逻辑: 在容器里跑 tools/*.py ───────────────────────
#
# 挂两个卷: 装机目录 (读写) 和 tools (只读)。
#
# ⚠ 不传 --user。setup.sh 那边传 `--user $(id -u):$(id -g)` 是为了让 Linux
#   上的产物属主是当前用户; Windows 上没有 uid 这个概念, Docker Desktop 通过
#   9p/virtiofs 转译文件属主, 传了反而会让容器里的进程失去写权限。
function Invoke-Tool {
    param([string]$Script, [string[]]$Args)
    $full = @(
        'run', '--rm',
        '-v', "${MountDir}:/work",
        '-v', "${MountDir}/tools:/tools:ro",
        $IdentityImage,
        'python3', "/tools/$Script", '--dir', '/work'
    ) + $Args
    & docker @full
    return $LASTEXITCODE
}

# ── 2. 生成 / 升级 .env ───────────────────────────────────
#
# 全部判断在 tools/envgen.py 里 —— 跟 setup.sh 同一份实现。
# 那里面几条不变量都是踩出来的: 先捞旧值再覆盖 / PG_PASSWORD 换了连不上
# 已有的库 / CATFISH_SECRET_KEY 换了库里所有 API key 全部解不开。
$envArgs = @(
    '--server-ip', $ServerIp,
    '--https', $EnableHttps,
    '--https-port', $HttpsPort,
    '--upgrade', $Upgrade
)
if ($GatewayWorkers)  { $envArgs += @('--gateway-workers', $GatewayWorkers) }
if ($IdentityWorkers) { $envArgs += @('--identity-workers', $IdentityWorkers) }
if ($env:CATFISH_IDENTITY_URL) { $envArgs += @('--identity-url', $env:CATFISH_IDENTITY_URL) }

# 正在跑的 catfish 容器属于哪个 compose 项目 —— 升级时新容器必须接上它的卷
# (9/23 现场: 包目录改名后 compose 当成新项目, 新建了 5 个空卷; 见 envgen)。
$existingProject = (docker inspect catfish-postgres --format '{{ index .Config.Labels "com.docker.compose.project" }}' 2>$null)
if ($LASTEXITCODE -ne 0) { $existingProject = '' }
$existingProject = "$existingProject".Trim()
$envArgs += @('--project-name', $existingProject)

# 既有 pgdata 卷 —— 查卷要 docker, 所以在这边查; 判断在 envgen 里, 免得
# 两个平台各写一遍"有卷但没密码该怎么办"。有现成项目名就只认它的卷。
if ($existingProject) {
    $pgVol = docker volume ls -q 2>$null | Where-Object { $_ -eq "${existingProject}_pgdata" } | Select-Object -First 1
} else {
    $pgVol = docker volume ls -q 2>$null | Where-Object { $_ -match '_pgdata$' } | Select-Object -First 1
}
if ($pgVol) { $envArgs += @('--pg-volume', $pgVol) }

if ($RegenEnvOnly -eq '1') {
    # 用退出码判断, 不靠 stdout 有没有输出 —— docker 的输出格式会变, 退出码不会。
    docker image inspect $IdentityImage 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Die "RegenEnvOnly 需要 catfish-identity 镜像在本地" @(
            "先跑一次完整装机 (会 load 镜像), 之后才能只重生成 .env。"
        )
    }
}
if ((Invoke-Tool 'envgen.py' $envArgs) -ne 0) { Die ".env 生成失败 (详情见上)" }

# .env 里的项目名导出给后面所有 docker compose 调用 (compose 自己也读 .env,
# 但显式导出一遍, 让"用哪套卷"只有一个来源)。
$env:COMPOSE_PROJECT_NAME = Get-EnvValue 'COMPOSE_PROJECT_NAME'
if (-not $env:COMPOSE_PROJECT_NAME) { Die ".env 里没有 COMPOSE_PROJECT_NAME (envgen 应该写了)" }

if ($RegenEnvOnly -eq '1') {
    Say ""
    Say "✓ .env 已重生 · RegenEnvOnly 模式退出"
    Say "  下一步 · 重启涉及服务:"
    Say "    docker compose up -d --force-recreate identity gateway web"
    exit 0
}

# ── 2.5 · identity 的 users.yaml / clients.yaml ───────────
if ((Invoke-Tool 'seedgen.py' @('--admin-password', $AdminPassword)) -ne 0) {
    Die "identity 种子配置生成失败 (详情见上)"
}

# ── 3. HTTPS 自签证书 ─────────────────────────────────────
#
# Docker 会自己造 certs 目录, 先建好省事。HTTP 模式下目录是空的,
# web 容器的 entrypoint 检测不到证书会降级只跑 :80, 不会反复重启。
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir 'certs') | Out-Null
if ($EnableHttps -eq '1') {
    Say "→ 证书 (SAN 含 $ServerIp)"
    $rc = & docker run --rm `
        -v "${MountDir}/certs:/certs" `
        -v "${MountDir}/tools:/tools:ro" `
        $IdentityImage `
        python3 /tools/certgen.py --ip $ServerIp --out /certs
    if ($LASTEXITCODE -ne 0) { Die "证书生成失败 (详情见上)" }
}

# ── 5. docker compose up ──────────────────────────────────
#
# --force-recreate: 不加的话 compose 对已存在的容器只做 restart, 而环境变量
# 是**建容器时**读的, 老值不会换 —— 现场会反复怀疑"为啥 workers 还是 4 /
# CORS 还挂"。
$composeArgs = @('-f', 'docker-compose.yml')
if ($EnableHttps -eq '1') {
    $composeArgs += @('-f', 'docker-compose.https.yml')

    # 443 被别人占着的话 web 容器起不来 (bind: address already in use), 而且
    # 会连累整个 stack。但**我们自己**的 web 容器占着是正常的 ——
    # force-recreate 会先释放旧容器的端口再重绑, 那种情况不该拦。
    $inUse = Get-NetTCPConnection -State Listen -LocalPort ([int]$HttpsPort) -ErrorAction SilentlyContinue
    if ($inUse) {
        $ours = docker ps --filter 'name=catfish-web' --format '{{.Ports}}' 2>$null |
            Where-Object { $_ -match ":$HttpsPort->" }
        if ($ours) {
            Say "→ $HttpsPort 由现有 catfish-web 容器占用 · force-recreate 会自动接管"
        } else {
            $pids = ($inUse | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
            Die "宿主机 $HttpsPort 端口已被**其它程序**占用 · web 容器会起不来" @(
                "查是谁占着:  Get-Process -Id $pids",
                "二选一:",
                "  A. 停掉占用方 (常见是 IIS / Apache):",
                "       Stop-Service W3SVC     # IIS",
                "  B. 换端口重跑 (员工则访问 https://${ServerIp}:8443):",
                "       .\setup.ps1 -ServerIp $ServerIp -HttpsPort 8443"
            )
        }
    }
}

Say ""
Say "→ docker compose up --force-recreate ..."
& docker compose @composeArgs up -d --force-recreate
if ($LASTEXITCODE -ne 0) { Die "docker compose up 失败 (详情见上)" }

# ── 6. verify ─────────────────────────────────────────────
$scheme = if ($EnableHttps -eq '1') { 'https' } else { 'http' }
$suffix = if ($EnableHttps -eq '1' -and $HttpsPort -ne '443') { ":$HttpsPort" } else { '' }
$WebUrl = "${scheme}://${ServerIp}${suffix}"

# 自签证书: Invoke-WebRequest 默认拒绝。-SkipCertificateCheck 只在 pwsh 6+
# 有; Windows 自带的 5.1 要改 ServicePointManager。两条都留着, 否则在
# 5.1 上这一整段验证会假红。
$Skip = @{}
if ($PSVersionTable.PSVersion.Major -ge 6) {
    $Skip = @{ SkipCertificateCheck = $true }
} elseif ($EnableHttps -eq '1') {
    Add-Type @"
using System.Net;using System.Security.Cryptography.X509Certificates;
public class CatfishCertPolicy : ICertificatePolicy {
  public bool CheckValidationResult(ServicePoint s, X509Certificate c, WebRequest r, int p) { return true; }
}
"@ -ErrorAction SilentlyContinue
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object CatfishCertPolicy
}

function Test-Url {
    param([string]$Url, [int]$TimeoutSec = 8)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing @Skip
        return $r
    } catch { return $null }
}

function Wait-Ready {
    param([string]$Name, [string]$Url, [int]$MaxSec = 180)
    Write-Host "  等 $Name 就绪 " -NoNewline
    $t = 0
    while ($t -lt $MaxSec) {
        if (Test-Url $Url 3) { Write-Host " ✓ ${t}s" -ForegroundColor Green; return $true }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 3
        $t += 3
    }
    Write-Host " ✗ 超时 ${MaxSec}s" -ForegroundColor Red
    return $false
}

Say ""
Wait-Ready 'identity' "$WebUrl/.well-known/openid-configuration" 180 | Out-Null
Wait-Ready 'gateway'  "http://${ServerIp}:8999/healthz"          180 | Out-Null

Say ""
Say "── verify ──"

$r = Test-Url "$WebUrl/.well-known/openid-configuration"
if ($r) {
    $iss = ([regex]'"issuer":"([^"]+)"').Match($r.Content)
    Say "  identity discovery: $(if ($iss.Success) { $iss.Groups[1].Value } else { '✓ 200' })"
} else {
    Write-Host "  identity discovery: ❌ 挂 · docker logs catfish-identity" -ForegroundColor Red
}

if (Test-Url "http://${ServerIp}:8999/healthz") {
    Say "  gateway healthz:    ✓ 200"
} else {
    Write-Host "  gateway healthz:    ❌ 挂 · docker logs catfish-gateway" -ForegroundColor Red
}

$cfg = Test-Url "$WebUrl/config.js"
if ($cfg -and $cfg.Content -match [regex]::Escape($ServerIp)) {
    Say "  web /config.js:     ✓ oidcIssuer 含 $ServerIp"
} else {
    SayWarn "web /config.js 待查 · docker exec catfish-web cat /usr/share/nginx/html/config.js"
}

# 内部服务反代 —— 这三跳是 7/29 现场那个坑的位置: gateway 容器里的
# 127.0.0.1 指向它自己, 不走 compose 服务名就是 502。症状只在「系统管理」
# 页显示"不可达", 登录/聊天/门户全正常, 没人会第一时间点开那一页。
$proxyFail = @()
# 写成 "名字=路径" 的字符串再拆 —— PowerShell 的嵌套数组在某些上下文会被
# 摊平成一维, 那样 $probe[1] 拿到的是字符而不是路径, 而且不会报错。
foreach ($probe in @('mcp=/v1/mcp/registry', 'hub=/v1/hub/healthz', 'wiki=/v1/wiki/healthz')) {
    $name, $path = $probe -split '=', 2
    if (-not (Test-Url "http://${ServerIp}:8999$path" 8)) { $proxyFail += $name }
}
if ($proxyFail.Count -eq 0) {
    Say "  内部服务反代:       ✓ mcp / hub / wiki 三跳都通"
} else {
    SayWarn "内部服务反代有问题: $($proxyFail -join ' ')"
    Say "     查 llm-gateway/config/models.yaml 里 mcp_registry / skills_hub /"
    Say "     wiki_hub 的 upstream_url 是不是 compose 服务名 (不能是 127.0.0.1)。"
}

# ── 7. 记下"这次装的到底是哪一版" ─────────────────────────
#
# 六个镜像的 tag 长期相同, `docker images` 看不出装的是哪一版。tag 那条
# 另外治 (central/VERSION + CI 检查), 但 tag 靠人守纪律, **digest 是内在的**。
# 写成文件: 装完半个月后来查的人不会有当时的终端。
$lines = @(
    "# Catfish 中央端装机记录 —— setup.ps1 自动生成, 请勿手改",
    "installed_at=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')",
    "installed_by=setup.ps1 (Windows)",
    "server_ip=$ServerIp",
    "https=$EnableHttps"
)
if (Test-Path (Join-Path $ScriptDir 'BUILD-INFO.txt')) {
    $lines += "# ↓ 来自交付包的 BUILD-INFO.txt"
    $lines += (Get-Content (Join-Path $ScriptDir 'BUILD-INFO.txt') | ForEach-Object { "package_$_" })
}
$lines += "# ↓ 每个服务实际跑的镜像 (tag 可能重名, image_id 不会)"
foreach ($img in $ComposeImages) {
    $id = docker image inspect $img --format '{{.Id}}' 2>$null
    $created = docker image inspect $img --format '{{.Created}}' 2>$null
    if (-not $id) { $id = '<查不到>' }
    $lines += "image=$img id=$($id -replace '^sha256:', '') created=$created"
}
$lines | Set-Content -Path (Join-Path $ScriptDir 'INSTALLED-BUILD.txt') -Encoding UTF8

Say ""
Say "── 这次装的是哪一版 ──"
Say "  已写入 INSTALLED-BUILD.txt (报障时把这个文件发给我们)"
foreach ($img in $ComposeImages) {
    $id = docker image inspect $img --format '{{.Id}}' 2>$null
    $short = if ($id) { $id.Substring(7, 12) } else { '??' }
    Say ("    {0,-34} {1}" -f $img, $short)
}
SayWarn "tag 相同不代表镜像相同 —— 认上面那串 id, 不是认 tag。"

# ── 8. 删旧镜像 (9/23) ── 跟 setup.sh 同一条规则: 只删 catfish-* 且 tag 不在
# 本次 compose 里的; 放在 verify 全绿之后; rmi 失败只报不停。
Say ""
Say "── 清理旧镜像 ──"
$keep = $ComposeImages | Where-Object { $_ -like 'catfish-*' }
$removed = 0
$all = docker images --format '{{.Repository}}:{{.Tag}}' 2>$null | Where-Object { $_ -like 'catfish-*' } | Sort-Object
foreach ($img in $all) {
    if ($keep -contains $img) { continue }
    docker rmi $img 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Say "  已删 $img"; $removed++ }
    else { SayWarn "删不掉 $img (还有容器引着它? docker ps -a 看看)" }
}
if ($removed -eq 0) { Say "  没有旧镜像要删" }
docker image prune -f 2>&1 | Out-Null

Say ""
Say "═══════════════════════════════════════════════════════"
Say "  装完了。员工访问: $WebUrl"
Say "  默认 sysadmin: admin@catfish.com / $AdminPassword"
Say "  ⚠ 首次登进立即改密"
if ($EnableHttps -eq '1') {
    Say ""
    Say "  ★ 要发给员工的是 certs\ca.pem (内部 CA), **不是** cert.pem。"
    Say "    cert.pem 是服务器证书, 每年会换; ca.pem 十年有效, 只装一次。"
    Say "    浏览器: 把 ca.pem 推进系统信任库 (域控组策略 / Intune)。"
    Say "    Companion 桌面端: 复制到员工机器的 ~\.catfish\server-ca.pem 即可"
    Say "      (不需要管理员权限)。不处理的话仪表盘显示「中央门户连不上」,"
    Say "      而同一地址浏览器打得开 —— 极易误判成网络问题。"
    Say ""
    Say "  ⚠ 服务器证书 397 天到期, 到期前在服务器上重跑 .\setup.ps1 自动重签。"
    Say "    CA 不变, 员工机器上的 ca.pem 不用动。"
} else {
    Say ""
    SayWarn "走 HTTP · 员工用**裸 IP**访问时前端会卡在「加载中…」且不报错"
    Say "     原因: oidc-client 的 PKCE 要 crypto.subtle, 浏览器只在安全上下文提供。"
    Say "     出路: 重跑并开 HTTPS   .\setup.ps1 -ServerIp $ServerIp -EnableHttps 1"
}
Say "═══════════════════════════════════════════════════════"
