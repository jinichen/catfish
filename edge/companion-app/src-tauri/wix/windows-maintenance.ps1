# Shared by MSI (embedded GUI helper) and Companion startup. No user data deletion.
# Dot-source this file to test selectors without touching processes or registrations.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-ExactPath([string]$Left, [string]$Right) {
    if (-not $Left -or -not $Right) { return $false }
    try { return [IO.Path]::GetFullPath($Left).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Right).TrimEnd('\') }
    catch { return $false }
}

function Test-WrapperCommand([string]$Command, [string]$Wrapper) {
    # Only the exact, quoted legacy path; never match arbitrary '*catfish*'.
    return $Command -and $Command.IndexOf(('"' + $Wrapper + '"'), [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-OwnedRoot($Process, [string[]]$AppExecutables, [string]$Wrapper, [string[]]$RuntimePythons) {
    foreach ($exe in $AppExecutables) {
        if (Test-ExactPath $Process.ExecutablePath $exe) { return $true }
    }
    if ($Process.Name -ieq 'cmd.exe' -and (Test-WrapperCommand $Process.CommandLine $Wrapper)) { return $true }
    foreach ($python in $RuntimePythons) {
        if ((Test-ExactPath $Process.ExecutablePath $python) -and
            $Process.CommandLine -match '(?i)(?:^|\s)-m\s+(?:catfish_search\.cli\s+watch|catfish_tool_bridge(?:\.\w+)*)(?:\s|$)') {
            return $true
        }
    }
    return $false
}

function Test-CurrentUserTask($Task, [string]$Sid, [string]$Wrapper) {
    if ($Task.TaskName -cne 'CatfishSearchWatcher' -or $Task.TaskPath -ne '\') { return $false }
    try {
        $principal = $Task.Principal.UserId
        if ($principal -notmatch '^S-1-') {
            $principal = ([Security.Principal.NTAccount]$principal).Translate([Security.Principal.SecurityIdentifier]).Value
        }
        if ($principal -ne $Sid) { return $false }
    } catch { return $false }
    $actions = @($Task.Actions)
    return $actions.Count -eq 1 -and
        [IO.Path]::GetFileName($actions[0].Execute) -match '^(?i:cmd)(?:\.exe)?$' -and
        (Test-WrapperCommand $actions[0].Arguments $Wrapper)
}

function Invoke-CatfishMaintenance([string]$Mode, [string]$InstallDir, [int]$ExcludePid = 0,
    [string]$StartupDir = [Environment]::GetFolderPath('Startup')) {
    if ($Mode -notin @('startup', 'install', 'uninstall')) { throw 'Invalid maintenance mode' }
    if (-not [IO.Path]::IsPathRooted($InstallDir)) { throw 'InstallDir must be absolute' }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $auditRoot = Join-Path $env:LOCALAPPDATA 'CatfishMaintenance'
    New-Item -ItemType Directory -Force -Path $auditRoot | Out-Null
    $backup = Join-Path $auditRoot ([guid]::NewGuid().ToString('N'))
    $wrapper = Join-Path $env:APPDATA 'catfish\catfish-search-watcher.bat'
    $apps = @((Join-Path $InstallDir 'catfish-companion-app.exe'),
        (Join-Path $env:LOCALAPPDATA 'Catfish Companion\catfish-companion-app.exe'))
    $pythons = @((Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe'))
    if ($env:HERMES_HOME) { $pythons += Join-Path $env:HERMES_HOME 'hermes-agent\venv\Scripts\python.exe' }

    # Snapshot before ending the task: /End alone can leave orphaned descendants.
    $processes = @(Get-CimInstance Win32_Process)
    $owned = @{}
    foreach ($process in $processes) {
        if ($process.ProcessId -eq $ExcludePid -or $process.ProcessId -eq $PID) { continue }
        if (-not (Test-OwnedRoot $process $apps $wrapper $pythons)) { continue }
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
        if ($owner -and $owner.ReturnValue -eq 0 -and $owner.Sid -eq $sid) { $owned[[int]$process.ProcessId] = $process }
    }
    # Only console-runtime descendants of verified roots, never all Python processes.
    do {
        $added = $false
        foreach ($process in $processes) {
            $id = [int]$process.ProcessId
            if ($id -eq $ExcludePid -or $id -eq $PID -or $owned.ContainsKey($id)) { continue }
            if (-not $owned.ContainsKey([int]$process.ParentProcessId)) { continue }
            if ($process.CreationDate -lt $owned[[int]$process.ParentProcessId].CreationDate) { continue }
            if ($process.Name -notmatch '^(?i:pythonw?|node|cmd|powershell|uv|hermes|catfish-email|catfish-wechat-reader)\.exe$') { continue }
            $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
            if ($owner -and $owner.ReturnValue -eq 0 -and $owner.Sid -eq $sid) { $owned[$id] = $process; $added = $true }
        }
    } while ($added)

    try {
        $tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -eq 'CatfishSearchWatcher' })
    } catch {
        # A disabled Task Scheduler must not prevent normal app use. MSI still fails
        # explicitly, rather than claiming a successful uninstall with unknown tasks.
        if ($Mode -ne 'startup') { throw }
        Write-Output "Warning: task inventory unavailable; migration will retry next startup: $_"
        $tasks = @()
    }
    foreach ($task in $tasks) {
        if (-not (Test-CurrentUserTask $task $sid $wrapper)) {
            Write-Output 'Skipped task with unverified ownership/action: CatfishSearchWatcher'
            continue
        }
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath |
            Set-Content -LiteralPath (Join-Path $backup 'CatfishSearchWatcher.xml') -Encoding UTF8
        Disable-ScheduledTask -InputObject $task | Out-Null
        Stop-ScheduledTask -InputObject $task
        Unregister-ScheduledTask -InputObject $task -Confirm:$false
        Write-Output 'Removed verified legacy task: CatfishSearchWatcher (XML backed up)'
    }
    # Stop app/watchdog first, then its workers; revalidate creation time against PID reuse.
    foreach ($process in @($owned.Values | Sort-Object CreationDate)) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)"
        if ($current -and $current.CreationDate -eq $process.CreationDate -and
            (Test-ExactPath $current.ExecutablePath $process.ExecutablePath)) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            if (Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)" |
                Where-Object { $_.CreationDate -eq $process.CreationDate }) {
                throw "Could not stop owned process PID=$($process.ProcessId)"
            }
            Write-Output "Stopped owned process: $($process.Name) PID=$($process.ProcessId)"
        }
    }
    # Only our exact legacy startup wrapper; never delete arbitrary shortcuts or data.
    $legacyFiles = @($wrapper, (Join-Path $StartupDir 'catfish-search-watcher.bat'))
    foreach ($file in $legacyFiles) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $text = Get-Content -LiteralPath $file -Raw
            if ($text -match 'catfish-search watcher wrapper' -and $text -match 'catfish_search\.cli watch') {
                New-Item -ItemType Directory -Force -Path $backup | Out-Null
                Copy-Item -LiteralPath $file -Destination (Join-Path $backup (([guid]::NewGuid().ToString('N')) + '.bat'))
                Remove-Item -LiteralPath $file
                Write-Output 'Removed verified legacy BAT (backed up)'
            }
        }
    }
    # No service is installed by this codebase. Do not delete guessed service names.
    # On uninstall only, remove exact Companion Run values pointing at this install.
    if ($Mode -eq 'uninstall') {
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        if (Test-Path $key) {
            $values = Get-ItemProperty -LiteralPath $key
            foreach ($name in @('Catfish Companion', 'catfish-companion-app')) {
                $value = $values.PSObject.Properties[$name]
                if ($value -and ($value.Value -eq ('"' + $apps[0] + '"') -or $value.Value -eq $apps[0])) {
                    New-Item -ItemType Directory -Force -Path $backup | Out-Null
                    @{ Name=$name; Value=$value.Value; Key=$key } | ConvertTo-Json |
                        Set-Content -LiteralPath (Join-Path $backup ($name + '.json')) -Encoding UTF8
                    Remove-ItemProperty -LiteralPath $key -Name $name
                }
            }
        }
    }
    Write-Output "Maintenance complete: $Mode. User mail, knowledge and runtime data preserved."
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-CatfishMaintenance $env:CATFISH_MAINTENANCE_MODE $env:CATFISH_INSTALL_DIR ([int]$env:CATFISH_EXCLUDE_PID)
}
