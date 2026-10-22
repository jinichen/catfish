<#
    服务器证书自动续期 (Windows + Docker Desktop, 10/22) —— renew-cert.sh 的 PowerShell 版。
    setup.ps1 装机时注册成计划任务 CatfishCertRenew (每天 03:17, 错过了开机后补跑)。重复跑没副作用。

    服务器证书只有 397 天 (macOS 不认超过 398 天的 TLS 证书), 原来要人记得在到期前重跑 setup.ps1。
    跟 setup.ps1 第 4 步同一份逻辑 (tools/certgen.py, 跑在 identity 镜像里): 剩不到 30 天 / SAN 里
    没有当前 IP 才重签服务器证书; 内部 CA 不动, 员工机器上的 ca.pem 不用换。真重签了才让 web 容器的
    nginx 重新加载证书。记录在 logs\cert-renew.log。
#>
$ErrorActionPreference = 'Continue'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'logs') | Out-Null
$Log = Join-Path $Dir 'logs\cert-renew.log'
function Log { param($m) $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"; Write-Host $line; Add-Content -Path $Log -Value $line -Encoding UTF8 }

$envFile = Join-Path $Dir '.env'
if (-not (Test-Path $envFile)) { Log "没有 .env (还没装机), 跳过"; exit 0 }
$issuerLine = Get-Content $envFile | Where-Object { $_ -match '^CATFISH_OIDC_ISSUER=' } | Select-Object -Last 1
$issuer = if ($issuerLine) { ($issuerLine -split '=', 2)[1].Trim().Trim('"', "'") } else { '' }
if ($issuer -notmatch '^https://') { Log "HTTP 模式 (没用证书), 跳过"; exit 0 }
$serverIp = ($issuer -replace '^https://', '') -replace '[:/].*$', ''
if ($serverIp -notmatch '^(\d{1,3}\.){3}\d{1,3}$') { Log "❌ 从 .env 的 CATFISH_OIDC_ISSUER ($issuer) 取不到服务器 IP"; exit 1 }
$cert = Join-Path $Dir 'certs\cert.pem'
if (-not (Test-Path $cert)) { Log "certs\cert.pem 不存在 (证书还没生成过, 由 setup.ps1 生成), 跳过"; exit 0 }

$image = (& docker inspect catfish-identity --format '{{.Config.Image}}' 2>$null)
if (-not $image) {
    $m = Select-String -Path (Join-Path $Dir 'docker-compose.yml') -Pattern '^\s*image:\s*(catfish-identity:\S+)' | Select-Object -First 1
    if ($m) { $image = $m.Matches[0].Groups[1].Value }
}
if (-not $image) { Log "❌ 找不到 catfish-identity 镜像 (Docker Desktop 没启动?)"; exit 1 }

# 同 setup.ps1: 挂载路径用正斜杠 (带空格的路径反斜杠形式 docker 解析会错)
$mount = $Dir -replace '\\', '/'
$before = (Get-FileHash $cert).Hash
$out = & docker run --rm -v "${mount}/certs:/certs" -v "${mount}/tools:/tools:ro" $image python3 /tools/certgen.py --ip $serverIp --out /certs 2>&1
if ($LASTEXITCODE -ne 0) { Log ("❌ 证书检查 / 重签失败: " + (($out | ForEach-Object { "$_" }) -join ' | ')); exit 1 }
if ((Get-FileHash $cert).Hash -eq $before) {
    $left = ($out | ForEach-Object { "$_" } | Select-String -Pattern '(\d+) 天后到期' | Select-Object -Last 1)
    Log "证书还有效, 不用续 $(if ($left) { $left.Matches[0].Value })"
    exit 0
}
& docker exec catfish-web nginx -s reload 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Log "✓ 服务器证书已续期, 门户已换上新证书 (内部 CA 没变, 员工电脑不用动)"
} else {
    Log "⚠ 服务器证书已续期, 但 web 容器的 nginx 没能重新加载 —— 重启一下: docker restart catfish-web"
    exit 1
}
