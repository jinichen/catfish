<#
.SYNOPSIS
    Catfish 中央服务 Windows 一键装机 —— 不需要 Docker Desktop (WSL2 路径)

.DESCRIPTION
    10/20 加。客户 Windows 机器上没有 Docker 时 setup.ps1 转到这里:

      1. 体检: 管理员 / 64 位 / 系统版本 (Win10 21H2+、Win11、Server 2022) / BIOS 虚拟化
      2. 开 Windows 功能「适用于 Linux 的 Windows 子系统」+「虚拟机平台」(首次要重启一次, 重启后自动接着装)
      3. 没有新版 WSL 就装包里带的 wsl.x64.msi (离线)
      4. 导入包里带的 Linux (Debian 12 + Docker 引擎) 为发行版 CatfishCentral
      5. 在里面跑跟 Linux 服务器同一个 setup.sh (镜像 / 配置 / 证书 / 自检全复用, 不另维护一份)
      6. 开机自启 (计划任务) + 局域网端口转发 (443 / 5173 / 8998 / 8999) + 防火墙放行

    为什么不用 Docker Desktop: 员工 > 250 人或年收入 > 1000 万美元的公司要买订阅, 客户多半在这个范围。

    装好以后:
      · 数据在 <InstallDir>\wsl\ext4.vhdx (数据库、上传文件都在里面), 备份就备份这个目录
      · 开机自动起; 手动停: Stop-ScheduledTask CatfishCentral; wsl --terminate CatfishCentral
      · 日志: <InstallDir>\logs\

.PARAMETER ServerIp      员工访问用的本机局域网 IP。不传则自动探测并确认。
.PARAMETER LocalOnly     1 = 单机模式: 服务器 IP 用 127.0.0.1, 只有这台电脑自己能用 (客户端也装在这台), 不开防火墙
.PARAMETER EnableHttps   1 = 开 443 (默认); 0 = 只跑 HTTP (裸 IP 下员工浏览器登录会卡住, 仅测试用)
.PARAMETER Upgrade       1 = 升级: 用新包覆盖程序文件, 保留 .env / 证书 / 账号 / 数据库
.PARAMETER InstallDir    WSL 磁盘、开机脚本、日志放哪 (默认 C:\CatfishCentral, 要有 20GB 以上空间)
.PARAMETER AdminPassword 门户初始管理员密码 (默认 catfish_2026, 装完立刻改)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1          # 自动判断走这条路
    powershell -ExecutionPolicy Bypass -File .\windows\setup-wsl.ps1 -ServerIp 192.168.1.20
#>
[CmdletBinding()]
param(
    [string]$ServerIp = $env:SERVER_IP,
    [ValidateSet('0', '1')][string]$EnableHttps = $(if ($env:ENABLE_HTTPS) { $env:ENABLE_HTTPS } else { '1' }),
    [ValidateSet('0', '1')][string]$Upgrade = $(if ($env:UPGRADE) { $env:UPGRADE } else { '0' }),
    [ValidateSet('0', '1')][string]$LocalOnly = $(if ($env:LOCAL_ONLY) { $env:LOCAL_ONLY } else { '0' }),
    [string]$InstallDir = 'C:\CatfishCentral',
    [string]$AdminPassword = $(if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { 'catfish_2026' }),
    # 重启后由 RunOnce 带上, 不要手动传
    [switch]$Resume,
    # 提权后 / 重启后是新开的窗口, 出错时窗口一闪就关, 客户看不到原因 —— 这两种情况结束前等回车
    [switch]$KeepOpen
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
# PowerShell 7.3+ 才认 $PSNativeCommandUseErrorActionPreference; Windows 自带的 powershell 是 5.1,
# 那里只要外部命令往 stderr 写了字、而 stderr 又被重定向 (2>$null / 2>&1 / *>), 在 'Stop' 下就直接
# 抛错退出 —— 10/22 Win11 现场: 收件箱版 wsl.exe --version 打了一句「需要更新」, 装机脚本当场崩在
# 第一个检测上。外部命令一律包在这里跑: 只在这一条命令期间不把 stderr 当错误, $LASTEXITCODE 照常。
function Invoke-Native { param([scriptblock]$Cmd) $ErrorActionPreference = 'Continue'; & $Cmd }

# wsl.exe 默认输出 UTF-16, PowerShell 当 ANSI 读会变成每个字之间夹空字符 (发行版名对不上)。新版 WSL 认这个变量
$env:WSL_UTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Distro = 'CatfishCentral'
$TaskName = 'CatfishCentral'
$FwRule = 'Catfish Central'
# 员工要从别的机器访问的端口 (其余服务在 compose 里只绑 127.0.0.1, 不对外)
$Ports = @(443, 5173, 8998, 8999)

function Say     { param($m) Write-Host $m }
function SayOk   { param($m) Write-Host "  ✓ $m" -ForegroundColor Green }
function SayWarn { param($m) Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Pause-IfNewWindow { if ($KeepOpen -or $Resume) { [void](Read-Host "按回车关闭窗口") } }
function Die {
    param($m, [string[]]$hints = @())
    Write-Host ""
    Write-Host "❌ $m" -ForegroundColor Red
    foreach ($h in $hints) { Write-Host "   $h" }
    Write-Host ""
    Pause-IfNewWindow
    exit 1
}

$WinDir = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\catfish-poc\windows
$PkgDir = Split-Path -Parent $WinDir                            # ...\catfish-poc
$Rootfs = Join-Path $WinDir 'catfish-wsl-rootfs-amd64.tar.gz'
$WslMsi = Join-Path $WinDir 'wsl.x64.msi'
$BootSrc = Join-Path $WinDir 'catfish-wsl-boot.ps1'

Say "═══════════════════════════════════════════════════════"
Say "  Catfish 中央服务一键装机 (Windows · WSL2, 不需要 Docker Desktop)"
Say "═══════════════════════════════════════════════════════"

# ── 0. 管理员 ──────────────────────────────────────────────
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Say "→ 需要管理员权限, 正在请求提升 (弹出确认框点「是」)…"
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($MyInvocation.MyCommand.Path)`"",
                 '-EnableHttps', $EnableHttps, '-Upgrade', $Upgrade, '-InstallDir', "`"$InstallDir`"", '-KeepOpen')
    if ($ServerIp) { $argList += @('-ServerIp', $ServerIp) }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argList
    exit 0
}

# ── 1. 体检 ────────────────────────────────────────────────
foreach ($f in @($Rootfs, $WslMsi, $BootSrc, (Join-Path $PkgDir 'setup.sh'))) {
    if (-not (Test-Path $f)) { Die "交付包不完整, 缺 $f" @("重新解压完整的 catfish-poc-FULL-amd64 包") }
}
if (-not [Environment]::Is64BitOperatingSystem) { Die "只支持 64 位 Windows" }

$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
$isServer = $os.ProductType -ne 1
# WSL2 + 新版 WSL (MSI): Win10 21H2 (19044)+ / Win11 (22000)+ / Server 2022 (20348)+。Server 2019 (17763) 不支持 WSL2
$okOs = if ($isServer) { $build -ge 20348 } else { $build -ge 19044 }
if (-not $okOs) {
    Die "这台 Windows 不支持 WSL2: $($os.Caption) (build $build)" @(
        "需要 Windows 10 21H2 及以上、Windows 11, 或 Windows Server 2022 及以上。",
        "Windows Server 2019 及更早的版本没有 WSL2, 这条装法用不了。")
}
SayOk "系统: $($os.Caption) (build $build)"

$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
# 已经开了 Hyper-V / 虚拟机平台时, 固件那项会显示 False (被系统占用了), 所以看两项
if (-not ($cs.HypervisorPresent -or $cpu.VirtualizationFirmwareEnabled)) {
    Die "CPU 虚拟化没开" @(
        "进 BIOS/UEFI 打开 Intel VT-x (或 AMD-V / SVM), 保存重启后再跑本脚本。",
        "如果这台本身是虚拟机: 在宿主机上给它开「嵌套虚拟化」(VMware: 虚拟化 Intel VT-x/EPT; Hyper-V: Set-VMProcessor -ExposeVirtualizationExtensions)。")
}
SayOk "CPU 虚拟化已开"

$freeGB = [math]::Floor((Get-PSDrive -Name ($InstallDir.Substring(0, 1))).Free / 1GB)
if ($freeGB -lt 20) { Die "$($InstallDir.Substring(0,2)) 剩余空间 ${freeGB}GB, 至少要 20GB" @("换个盘: -InstallDir D:\CatfishCentral") }

# ── 2. Windows 功能 (首次要重启) ─────────────────────────────
$needReboot = $false
foreach ($feat in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
    $st = (Get-WindowsOptionalFeature -Online -FeatureName $feat).State
    if ($st -ne 'Enabled') {
        Say "→ 开启 Windows 功能 $feat …"
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $feat -All -NoRestart
        if ($r.RestartNeeded) { $needReboot = $true }
    }
}
if ($needReboot) {
    if ($Resume) { Die "重启过一次了, Windows 功能还是要求重启" @("手动重启一次, 再跑: powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"") }
    # 重启后自动接着装 (登录后弹出这个窗口)
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Resume -KeepOpen -EnableHttps $EnableHttps -Upgrade $Upgrade -InstallDir `"$InstallDir`""
    if ($ServerIp) { $cmd += " -ServerIp $ServerIp" }
    Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'CatfishCentralSetup' -Value $cmd
    Say ""
    SayWarn "Windows 功能已开启, 需要重启一次才生效。重启并登录后会自动继续安装。"
    $ans = Read-Host "  现在重启? (y/n · 回车 = y)"
    if (-not $ans -or $ans -match '^[yY]$') { Restart-Computer -Force }
    SayWarn "记得稍后手动重启; 重启登录后会自动继续。"
    Pause-IfNewWindow
    exit 0
}
SayOk "Windows 功能已开 (WSL + 虚拟机平台)"

# ── 3. 新版 WSL ─────────────────────────────────────────────
# 收件箱版 WSL 没有 `wsl --version`, 跑了返回非 0
Invoke-Native { & wsl.exe --version *> $null }
if ($LASTEXITCODE -ne 0) {
    Say "→ 安装 WSL $(Get-Content (Join-Path $WinDir 'wsl-msi-version.txt') -ErrorAction SilentlyContinue) (离线)…"
    $p = Start-Process msiexec.exe -ArgumentList @('/i', "`"$WslMsi`"", '/qn', '/norestart') -Wait -PassThru
    if ($p.ExitCode -notin @(0, 3010)) { Die "WSL 安装失败 (msiexec 退出码 $($p.ExitCode))" }
    Invoke-Native { & wsl.exe --version *> $null }
    if ($LASTEXITCODE -ne 0) { Die "WSL 装上了但还不能用" @("重启一次 Windows 后再跑本脚本") }
}
SayOk "WSL: $((& wsl.exe --version | Select-Object -First 1) -replace '\x00','')"

# ── 4. 服务器 IP ────────────────────────────────────────────
$cfgPath = Join-Path $InstallDir 'catfish-wsl.json'
if (-not $ServerIp -and $Upgrade -eq '1' -and (Test-Path $cfgPath)) {
    $ServerIp = (Get-Content $cfgPath -Raw | ConvertFrom-Json).ServerIp
}
# 10/28: 单机模式不探测, 直接用 127.0.0.1 (跟 setup.sh 同一规则); 升级时上次装的就是 127.x 也按单机模式走
if ($LocalOnly -eq '1' -and -not $ServerIp) { $ServerIp = '127.0.0.1' }
if ($Upgrade -eq '1' -and $ServerIp -match '^127\.') { $LocalOnly = '1' }
if (-not $ServerIp) {
    # 跟 setup.ps1 / setup.sh 同一组排除规则: WSL / Hyper-V 的虚拟网卡选中了员工连不上
    $cand = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notmatch '^(127\.|169\.254\.|172\.1[7-9]\.|172\.2[0-9]\.|172\.3[0-1]\.)' -and
            $_.InterfaceAlias -notmatch 'Loopback|WSL|vEthernet|Hyper-V'
        } | Sort-Object -Property SkipAsSource, InterfaceMetric | Select-Object -ExpandProperty IPAddress)
    if ($cand.Count -eq 0) { Die "无法自动探测本机 IP" @("显式指定: -ServerIp 192.168.x.x") }
    if ($cand.Count -gt 1) {
        Say "→ 探测到多个 IP, 选一个员工能访问到的:"
        for ($i = 0; $i -lt $cand.Count; $i++) { Say "    [$i] $($cand[$i])" }
        $pick = Read-Host "  序号 (回车 = 0)"
        if (-not $pick) { $pick = '0' }
        $ServerIp = $cand[[int]$pick]
    } else {
        $ServerIp = $cand[0]
        $ans = Read-Host "→ 员工用 $ServerIp 访问这台服务器, 对吗? (y/n · 回车 = y)"
        if ($ans -and $ans -notmatch '^[yY]$') { Die "请显式指定" @("-ServerIp 192.168.x.x") }
    }
}
if ($ServerIp -notmatch '^(\d{1,3}\.){3}\d{1,3}$') { Die "IP 格式不对: $ServerIp" }
# 10/22 现场有人填了 -ServerIp 127.0.0.1: 证书和登录地址都签给 127.0.0.1, 员工电脑上 127.0.0.1 是它自己, 全连不上
if ($ServerIp -match '^127\.' -and $LocalOnly -eq '1') {
    SayWarn "单机模式: 只有这台电脑自己能用 (客户端也要装在这台), 别的电脑连不上"
} elseif ($ServerIp -match '^(127\.|0\.0\.0\.0$|169\.254\.)') {
    Die "-ServerIp $ServerIp 只有这台电脑自己能访问, 员工电脑连不上" @(
        "填这台电脑的局域网 IP (命令行跑 ipconfig, 看「IPv4 地址」), 例: -ServerIp 192.168.1.20",
        "或者不写 -ServerIp, 脚本会自动探测并让你确认",
        "真的只在这台电脑上用 (客户端也装在这台): -LocalOnly 1")
}
SayOk "服务器 IP: $ServerIp"

# ── 5. 导入发行版 ───────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $InstallDir, (Join-Path $InstallDir 'logs') | Out-Null
$have = @(Invoke-Native { & wsl.exe -l -q 2>$null } | ForEach-Object { ($_ -replace '\x00', '').Trim() }) -contains $Distro
if (-not $have) {
    if ($Upgrade -eq '1') { Die "升级模式, 但没找到发行版 $Distro" @("首次安装别加 -Upgrade 1") }
    Say "→ 导入 Linux (Debian 12 + Docker 引擎) 到 $InstallDir\wsl …"
    & wsl.exe --import $Distro (Join-Path $InstallDir 'wsl') $Rootfs --version 2
    if ($LASTEXITCODE -ne 0) { Die "wsl --import 失败 (详情见上)" }
}
SayOk "发行版 $Distro 就绪"

function InDistro {
    param([string]$cmd)
    # -e: 参数原样交给 bash, 不经发行版默认 shell 再展开一遍 (用 -- 的话 $ 会被多展开一次)
    #
    # 10/22: 输出必须 | Out-Host。PowerShell 函数里没被接走的输出全算返回值 —— 原来 setup.sh 的
    # stdout (含它自己的报错说明) 全进了 $rc, 屏幕上只剩 docker 打在 stderr 的进度, 看不到错在哪;
    # 而且 $rc 成了一串行, `$rc -ne 0` 对数组是「有不等于 0 的元素」恒真, setup.sh 成功也报失败。
    & wsl.exe -d $Distro -u root -e bash -c $cmd | Out-Host
    return $LASTEXITCODE
}

# Windows 路径 → 发行版里的路径 (/mnt/c/...)
$PkgLinux = ((& wsl.exe -d $Distro -u root -e wslpath -a ($PkgDir -replace '\\', '/')) -replace '\x00', '').Trim()
if (-not $PkgLinux) { Die "换算不出交付包在 Linux 里的路径: $PkgDir" }
$ImageTar = Get-ChildItem -Path (Join-Path $PkgDir 'images') -Filter '*.tar.gz' -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -Last 1
if (-not $ImageTar) { Die "包里没有镜像 (images\*.tar.gz)" }
$ImageLinux = "$PkgLinux/images/$($ImageTar.Name)"

# ── 6. 交付包放进发行版 ─────────────────────────────────────
# 跟 Linux 升级同一个做法: 新包覆盖程序文件。.env / 证书 / users.yaml 这些装机时现场生成的不在包里, 自然保留。
# 镜像 (1.5GB) 不复制, setup.sh 直接从 C 盘读 (IMAGE_TAR)。
Say "→ 复制交付包到发行版 /opt/catfish/catfish-poc …"
$rc = InDistro "set -e; mkdir -p /opt/catfish/catfish-poc; tar -C '$PkgLinux' --exclude=./images --exclude=./windows -cf - . | tar -C /opt/catfish/catfish-poc -xf -; chmod -R a+rX /opt/catfish/catfish-poc; find /opt/catfish/catfish-poc -name '*.sh' -exec chmod 755 {} +"
if ($rc -ne 0) { Die "复制交付包失败" }
SayOk "交付包已就位"

# ── 7. 开机脚本 + 配置 + 计划任务 ───────────────────────────
$BootPs1 = Join-Path $InstallDir 'catfish-wsl-boot.ps1'
Copy-Item $BootSrc $BootPs1 -Force
[pscustomobject]@{ Distro = $Distro; ServerIp = $ServerIp; Ports = $Ports } |
    ConvertTo-Json | Set-Content -Path $cfgPath -Encoding UTF8

# 端口被别的程序占着 (常见: IIS 占 443) 就别往下走, 不然转发静默失败、员工打不开
# 只在还没有我们自己的转发时查 (重跑时这些端口本来就是我们的 iphlpsvc / wslrelay 在听)
$ours = (& netsh.exe interface portproxy show v4tov4) -join "`n"
foreach ($p in $Ports) {
    if ($ours -match "\b$p\b") { continue }
    $l = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($l) {
        $pn = (Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        if ($pn -notin @('wslrelay', 'svchost')) {
            Die "端口 $p 已被占用: $pn (PID $($l.OwningProcess))" @(
                "员工要通过这个端口访问中央服务, 请先停掉或改掉占用它的程序 (443 常见是 IIS: 停掉 W3SVC 服务)。")
        }
    }
}

# 端口转发要 IP Helper 服务
Set-Service -Name iphlpsvc -StartupType Automatic
Start-Service -Name iphlpsvc

if ($LocalOnly -eq '1') {
    # 单机模式本机访问不过防火墙, 不给局域网开口子
    SayOk "单机模式: 不开防火墙端口"
} else {
    if (-not (Get-NetFirewallRule -DisplayName $FwRule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $FwRule -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort ($Ports | ForEach-Object { "$_" }) -Profile Any | Out-Null
    }
    SayOk "防火墙已放行 TCP $($Ports -join ' / ')"
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    # WSL 发行版是按 Windows 用户登记的, 开机任务必须以本用户身份跑; 不登录也要起来 → 要存一次密码。
    Say ""
    Say "→ 设置开机自启: 服务器重启后不用登录也会自动起来, 需要当前账户的 Windows 登录密码 (只存在本机计划任务里)"
    $user = "$env:USERDOMAIN\$env:USERNAME"
    for ($try = 1; $try -le 3; $try++) {
        $cred = Get-Credential -UserName $user -Message "Catfish 中央服务开机自启 · 输入 $user 的 Windows 密码"
        if (-not $cred) { Die "没有输入密码, 开机自启没设上" @("重跑本脚本即可, 前面已完成的步骤会跳过") }
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BootPs1`""
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
        try {
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
                -User $user -Password $cred.GetNetworkCredential().Password -RunLevel Highest | Out-Null
            break
        } catch {
            SayWarn "注册失败 (多半是密码不对): $($_.Exception.Message)"
            if ($try -eq 3) {
                Die "开机自启没设上" @("账户只用 PIN / 没有设密码的话设不了「不登录也启动」: 给账户设个密码, 或换一个有密码的管理员账户运行本脚本。")
            }
        }
    }
}
SayOk "开机自启: 计划任务 $TaskName"

# 拉起 (dockerd + 端口转发 + 常驻), 等 Docker 引擎能用
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    if ((InDistro 'docker info >/dev/null 2>&1') -eq 0) { $ready = $true; break }
}
if (-not $ready) { Die "Docker 引擎没起来" @("看日志: $InstallDir\logs\boot.log", "以及: wsl -d $Distro -u root -- tail -50 /var/log/catfish/dockerd.log") }
SayOk "Docker 引擎就绪 (发行版里)"

# ── 8. 跑 setup.sh (跟 Linux 服务器同一个) ──────────────────
Say ""
Say "→ 安装中央服务 (setup.sh) …"
# 单引号包住传给 bash; 值里本身有单引号 (密码可能有) 要写成 '\'' 才不会断开
function Q { param([string]$v) "'" + ($v -replace "'", "'\''") + "'" }
$envs = "SERVER_IP=$(Q $ServerIp) ENABLE_HTTPS=$(Q $EnableHttps) UPGRADE=$(Q $Upgrade) LOCAL_ONLY=$(Q $LocalOnly) IMAGE_TAR=$(Q $ImageLinux) ADMIN_PASSWORD=$(Q $AdminPassword)"
$rc = InDistro "cd /opt/catfish/catfish-poc && $envs bash setup.sh"
if ($rc -ne 0) { Die "setup.sh 失败 (详情见上)" @("修好后直接重跑本脚本, 已完成的步骤会跳过") }

# ── 9. 从 Windows 这边验一遍 (走端口转发, 跟员工访问同一条路) ─────
Say ""
Say "→ 从 Windows 侧验证端口转发 …"
$checks = @(@{ n = 'gateway'; u = "http://${ServerIp}:8999/healthz" })
if ($EnableHttps -eq '1') { $checks += @{ n = '门户 (HTTPS)'; u = "https://$ServerIp/" } } else { $checks += @{ n = '门户'; u = "http://${ServerIp}:5173/" } }
$bad = 0
foreach ($c in $checks) {
    $code = Invoke-Native { & curl.exe -sk -m 10 -o NUL -w '%{http_code}' $c.u 2>$null }
    if ($code -eq '200') { SayOk "$($c.n): $($c.u) → 200" } else { SayWarn "$($c.n): $($c.u) → $code"; $bad++ }
}
if ($bad -gt 0) {
    SayWarn "Windows 侧访问不通: 看 $InstallDir\logs\boot.log 里的端口转发记录; netsh interface portproxy show v4tov4"
}

Say ""
Say "═══════════════════════════════════════════════════════"
Say "  装好了 · 员工浏览器打开: $(if ($EnableHttps -eq '1') { "https://$ServerIp" } else { "http://${ServerIp}:5173" })"
Say "  数据: $InstallDir\wsl\ext4.vhdx (备份这个目录)   日志: $InstallDir\logs\"
Say "  服务器重启后自动起来, 不用登录。"
Say "═══════════════════════════════════════════════════════"
Pause-IfNewWindow
