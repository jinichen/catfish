# Non-destructive native Windows regression: OS process/task mutations are mocked.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot '..\src-tauri\wix\windows-maintenance.ps1'
$tokens = $null; $parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
. $scriptPath
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
$savedLocal = $env:LOCALAPPDATA; $savedRoaming = $env:APPDATA
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('catfish-maintenance-test-' + [guid]::NewGuid().ToString('N'))
try {
    $env:LOCALAPPDATA = Join-Path $fixture 'Local'
    $env:APPDATA = Join-Path $fixture 'Roaming'
    $appDir = Join-Path $env:LOCALAPPDATA 'Catfish Companion'
    $startup = Join-Path $fixture 'Startup'
    $wrapper = Join-Path $env:APPDATA 'catfish\catfish-search-watcher.bat'
    New-Item -ItemType Directory -Force -Path $appDir, $startup, (Split-Path $wrapper) | Out-Null
    Set-Content -LiteralPath $wrapper -Value 'rem catfish-search watcher wrapper; python -m catfish_search.cli watch'
    $data = Join-Path $appDir 'user-data.md'
    Set-Content -LiteralPath $data -Value 'keep me'
    $script:sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $script:tasks = @([pscustomobject]@{
        TaskName='CatfishSearchWatcher'; TaskPath='\'; Principal=[pscustomobject]@{UserId=$script:sid}
        Actions=@([pscustomobject]@{Execute='cmd'; Arguments=('/c "' + $wrapper + '"')})
    })
    $script:processes = @(
        [pscustomobject]@{ProcessId=901001; ParentProcessId=0; Name='cmd.exe'; ExecutablePath='C:\Windows\System32\cmd.exe'; CommandLine=('cmd /c "' + $wrapper + '"'); CreationDate=[datetime]'2026-01-01'; Owner=$script:sid},
        [pscustomobject]@{ProcessId=901002; ParentProcessId=901001; Name='python.exe'; ExecutablePath='D:\Python\python.exe'; CommandLine='python -m catfish_search.cli watch'; CreationDate=[datetime]'2026-01-02'; Owner=$script:sid},
        [pscustomobject]@{ProcessId=901003; ParentProcessId=0; Name='python.exe'; ExecutablePath='D:\Python\python.exe'; CommandLine='python unrelated.py'; CreationDate=[datetime]'2026-01-02'; Owner=$script:sid},
        [pscustomobject]@{ProcessId=901004; ParentProcessId=0; Name='catfish-companion-app.exe'; ExecutablePath=(Join-Path $appDir 'catfish-companion-app.exe'); CommandLine='app'; CreationDate=[datetime]'2026-01-02'; Owner='S-1-5-99'}
    )
    $script:stopped = @(); $script:removed = 0
    function Get-CimInstance($ClassName, $Filter) {
        if ($Filter) { return $script:processes | Where-Object { "ProcessId=$($_.ProcessId)" -eq $Filter } }
        return $script:processes
    }
    function Invoke-CimMethod($InputObject, $MethodName) { return @{ReturnValue=0; Sid=$InputObject.Owner} }
    function Get-ScheduledTask { return $script:tasks }
    function Export-ScheduledTask($TaskName, $TaskPath) { return '<Task />' }
    function Disable-ScheduledTask($InputObject) {}
    function Stop-ScheduledTask($InputObject) {}
    function Unregister-ScheduledTask($InputObject, [switch]$Confirm) { $script:removed++; $script:tasks=@() }
    function Stop-Process($Id, [switch]$Force) {
        $script:stopped += $Id
        $script:processes = @($script:processes | Where-Object ProcessId -ne $Id)
    }
    Assert-True (Test-CurrentUserTask $script:tasks[0] $script:sid $wrapper) 'Legacy cmd action not recognized'
    Assert-True (-not (Test-WrapperCommand ('cmd /c "' + $wrapper + '.other"') $wrapper)) 'Path-prefix match is unsafe'
    Invoke-CatfishMaintenance 'install' $appDir 0 $startup
    Assert-True ($script:removed -eq 1) 'Task not removed'
    Assert-True (($script:stopped -join ',') -eq '901001,901002') 'Wrong processes stopped'
    Assert-True (Test-Path -LiteralPath $data) 'User data removed'
    Assert-True (-not (Test-Path -LiteralPath $wrapper)) 'Legacy wrapper remains'
    Assert-True (@(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'CatfishMaintenance') -Recurse -Filter '*.xml').Count -eq 1) 'Task backup missing'
    Invoke-CatfishMaintenance 'install' $appDir 0 $startup
    Assert-True ($script:removed -eq 1) 'Migration is not idempotent'
    Write-Output 'Windows maintenance regression passed (no real processes/tasks touched)'
} finally {
    $env:LOCALAPPDATA = $savedLocal; $env:APPDATA = $savedRoaming
    # Leave disposable fixtures for CI inspection; never run recursive user-directory cleanup.
    Write-Output "Test fixtures: $fixture"
}
