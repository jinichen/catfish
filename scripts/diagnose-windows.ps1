# Read-only diagnostics. Compatible with Windows PowerShell 5.1.
param([ValidateRange(10, 600)][int]$CaptureSeconds = 90)
$ErrorActionPreference = 'Continue'
$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { $desktop = $env:TEMP }
$report = Join-Path $desktop ('Catfish-Diagnostics-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $report -Force | Out-Null
function Save-Report($Name, $Value) {
    $Value | Out-String -Width 4096 | Out-File (Join-Path $report $Name) -Encoding utf8
}
function Get-Processes {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate
}
Save-Report 'system.txt' (Get-CimInstance Win32_OperatingSystem |
    Select-Object Caption, Version, BuildNumber, OSArchitecture)
$processes = @(Get-Processes)
$relevant = @($processes | Where-Object {
    $_.Name -match 'catfish|companion|hermes|foxmail|outlook|python|powershell|cmd.exe' -or
    $_.CommandLine -match 'catfish|companion|hermes'
})
Save-Report 'processes.txt' ($relevant | Format-List *)
Save-Report 'application-files.txt' ($relevant | Where-Object {
    $_.Name -match 'catfish|companion|foxmail|outlook' -and $_.ExecutablePath
} | ForEach-Object {
    Get-Item -LiteralPath $_.ExecutablePath -ErrorAction SilentlyContinue |
        Select-Object FullName, Length, LastWriteTime,
            @{n='FileVersion';e={$_.VersionInfo.FileVersion}},
            @{n='ProductVersion';e={$_.VersionInfo.ProductVersion}}
})
$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -match 'catfish|hermes|companion' -or
    ($_.Actions | Out-String) -match 'catfish|hermes|companion'
}
Save-Report 'scheduled-tasks.txt' ($tasks | Select-Object TaskName, TaskPath, State,
    @{n='Actions';e={$_.Actions | Out-String}}, @{n='User';e={$_.Principal.UserId}} | Format-List *)
Save-Report 'startup.txt' (Get-CimInstance Win32_StartupCommand -ErrorAction SilentlyContinue |
    Where-Object { $_.Command -match 'catfish|hermes|companion' } | Format-List *)

# Inspect candidate runtimes independently; do not assume one is the active runtime.
$homes = @($env:HERMES_HOME,
    [Environment]::GetEnvironmentVariable('HERMES_HOME', 'User'),
    [Environment]::GetEnvironmentVariable('HERMES_HOME', 'Machine'),
    (Join-Path $env:LOCALAPPDATA 'hermes'), (Join-Path $env:USERPROFILE '.hermes')) |
    Where-Object { $_ } | Select-Object -Unique
Save-Report 'runtime-candidates.txt' $homes
$index = 0
foreach ($runtime in $homes) {
    $index++
    $prefix = 'runtime-' + $index
    $cli = Join-Path $runtime 'hermes-agent\venv\Scripts\catfish-email.exe'
    Save-Report ($prefix + '-files.txt') (Get-Item -LiteralPath $cli -ErrorAction SilentlyContinue |
        Select-Object FullName, Length, LastWriteTime)
    if (Test-Path -LiteralPath $cli) {
        foreach ($probe in @(@('--help'), @('discover', '--json'))) {
            $label = if ($probe[0] -eq '--help') { 'help' } else { 'discover' }
            $out = Join-Path $report ($prefix + '-' + $label + '.txt')
            $err = Join-Path $report ($prefix + '-' + $label + '-stderr.txt')
            try {
                $info = New-Object System.Diagnostics.ProcessStartInfo
                $info.FileName = $cli
                $info.Arguments = $probe -join ' '
                $info.UseShellExecute = $false
                $info.CreateNoWindow = $true
                $info.RedirectStandardOutput = $true
                $info.RedirectStandardError = $true
                $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
                $info.StandardOutputEncoding = [Text.Encoding]::UTF8
                $info.StandardErrorEncoding = [Text.Encoding]::UTF8
                $p = New-Object System.Diagnostics.Process
                $p.StartInfo = $info
                [void]$p.Start()
                $stdoutTask = $p.StandardOutput.ReadToEndAsync()
                $stderrTask = $p.StandardError.ReadToEndAsync()
                if ($p.WaitForExit(45000)) {
                    $p.WaitForExit()
                    Save-Report ($prefix + '-' + $label + '-status.txt') ('Exit code: ' + $p.ExitCode)
                    $stdoutTask.Result | Out-File $out -Encoding utf8
                    $stderrTask.Result | Out-File $err -Encoding utf8
                } else {
                    # Only stop the diagnostic process created above.
                    Stop-Process -Id $p.Id -ErrorAction SilentlyContinue
                    Save-Report ($prefix + '-' + $label + '-status.txt') 'Timed out after 45 seconds.'
                }
            } catch { Save-Report ($prefix + '-' + $label + '-error.txt') $_ }
        }
    }
    $log = Join-Path $runtime 'logs\catfish-companion-bootstrap.log'
    if (Test-Path -LiteralPath $log) {
        Save-Report ($prefix + '-bootstrap.txt') (Get-Content -LiteralPath $log -Tail 200)
    } else { Save-Report ($prefix + '-bootstrap.txt') ('Missing: ' + $log) }
}
$maintenance = Join-Path $env:LOCALAPPDATA 'CatfishMaintenance\lifecycle.log'
if (Test-Path -LiteralPath $maintenance) {
    Save-Report 'maintenance.txt' (Get-Content -LiteralPath $maintenance -Tail 200)
}

# WMI start events capture short-lived children that ordinary process polling misses.
$source = 'CatfishDiagnostic-' + [Guid]::NewGuid().ToString('N')
$events = New-Object 'System.Collections.Generic.List[object]'
try {
    Register-WmiEvent -Class Win32_ProcessStartTrace -SourceIdentifier $source -ErrorAction Stop | Out-Null
    Write-Host "Capturing process starts for $CaptureSeconds seconds. Reproduce the black window now."
    Write-Host 'Open Companion and refresh its mail page during this interval.'
    $until = (Get-Date).AddSeconds($CaptureSeconds)
    while ((Get-Date) -lt $until) {
        $event = Wait-Event -SourceIdentifier $source -Timeout 1
        if (-not $event) { continue }
        $item = $event.SourceEventArgs.NewEvent
        $child = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $item.ProcessID) -ErrorAction SilentlyContinue
        $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $item.ParentProcessID) -ErrorAction SilentlyContinue
        $events.Add([pscustomobject]@{
            Time = (Get-Date).ToString('o'); Name = $item.ProcessName
            PID = $item.ProcessID; ParentPID = $item.ParentProcessID
            Path = $child.ExecutablePath; Command = $child.CommandLine
            ParentName = $parent.Name; ParentPath = $parent.ExecutablePath
            ParentCommand = $parent.CommandLine
        })
        Remove-Event -EventIdentifier $event.EventIdentifier
    }
} catch {
    Save-Report 'capture-error.txt' $_
    Write-Warning 'Process capture failed. Try running PowerShell as administrator for this part.'
} finally {
    Unregister-Event -SourceIdentifier $source -ErrorAction SilentlyContinue
    Get-Event -SourceIdentifier $source -ErrorAction SilentlyContinue | Remove-Event -ErrorAction SilentlyContinue
    Save-Report 'process-starts.txt' ($events | Format-List *)
}
Write-Host "Done. Report folder: $report"
Write-Host 'Reports can contain account names, local paths and command-line secrets. Review before sharing.'
Write-Host 'No mail bodies were requested. No installed components or startup entries were changed.'
