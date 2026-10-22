<#
    Catfish 中央服务开机脚本 (10/20) —— 计划任务 CatfishCentral 开机时以装机账户身份跑, 不用登录。
    setup-wsl.ps1 把它拷到 <InstallDir>\ 并写好同目录的 catfish-wsl.json。

    1. 发行版里拉起 Docker 引擎 (catfish-start.sh), 容器自己按 restart 策略起来
    2. 刷新端口转发: WSL2 是 NAT, 发行版的 IP 每次启动都会变, 局域网访问 Windows IP 的这几个端口要转进去
    3. 常驻: 保持一个 wsl.exe 连着发行版。WSL 在没有 wsl.exe 连着时几秒后就把发行版停掉 (连同 dockerd)
       常驻的那个 wsl.exe 每天跑一次 renew-cert.sh (服务器证书自动续期, 10/22)
#>
$ErrorActionPreference = 'Continue'
$env:WSL_UTF8 = '1'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Get-Content (Join-Path $Dir 'catfish-wsl.json') -Raw | ConvertFrom-Json
$Log = Join-Path $Dir 'logs\boot.log'
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null

function Log { param($m) "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Add-Content -Path $Log -Encoding UTF8 }

# 日志别无限长: 超过 2MB 留最后一半
if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 2MB) {
    $tail = Get-Content $Log -Tail 5000
    Set-Content -Path $Log -Value $tail -Encoding UTF8
}

while ($true) {
    Log "启动发行版 $($cfg.Distro) / Docker 引擎 (服务器 IP $($cfg.ServerIp))"
    $out = & wsl.exe -d $cfg.Distro -u root -e /usr/local/bin/catfish-start.sh $cfg.ServerIp 2>&1
    Log ("catfish-start: exit=$LASTEXITCODE " + (($out | ForEach-Object { "$_" -replace '\x00', '' }) -join ' | '))

    # 发行版 eth0 的 IP (NAT 内网地址)。-e: 参数原样交给 sh, $4 不会被发行版默认 shell 先吃掉
    $wslIp = ((& wsl.exe -d $cfg.Distro -u root -e sh -c "ip -4 -o addr show dev eth0 | awk '{print `$4}' | cut -d/ -f1") -replace '\x00', '').Trim()
    if ($wslIp -match '^(\d{1,3}\.){3}\d{1,3}$') {
        foreach ($p in $cfg.Ports) {
            & netsh.exe interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 *> $null
            & netsh.exe interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp *> $null
        }
        Log "端口转发 $($cfg.Ports -join ',') → $wslIp"
    } else {
        Log "拿不到发行版 IP ('$wslIp'), 端口转发没刷新"
    }

    # 常驻; 正常情况下不会返回。返回了 (发行版被停 / WSL 更新) 就 10 秒后从头再来。
    # 10/22: 常驻顺便每天检查一次服务器证书 —— 剩不到 30 天自动重签并让 nginx 换上 (renew-cert.sh,
    # 内部 CA 不动, 员工电脑不用管)。原来 397 天到期要人记得重跑装机脚本; WSL 里没有 cron。
    # 记录在发行版里的 /opt/catfish/catfish-poc/logs/cert-renew.log。
    & wsl.exe -d $cfg.Distro -u root -e bash -c 'cd /opt/catfish/catfish-poc 2>/dev/null; mkdir -p logs; while true; do [ -f renew-cert.sh ] && bash renew-cert.sh >> logs/cert-renew.log 2>&1; sleep 86400; done'
    Log "发行版连接断了 (exit=$LASTEXITCODE), 10 秒后重新拉起"
    Start-Sleep -Seconds 10
}
