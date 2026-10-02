# Piper TTS one-shot installer for Windows (10/2) -- the Windows twin of install-piper-tts.sh.
#
# Installs piper-tts into %USERPROFILE%\.catfish\piper-venv (Companion looks for
# Scripts\piper.exe there) and downloads the default Chinese voice into
# %USERPROFILE%\.catfish\piper-voices. Same layout as the macOS script, so the
# Companion code is shared.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File edge\companion-app\scripts\install-piper-tts.ps1
#   ... -NoVoice      skip the voice download (bring your own)
#
# Pure ASCII on purpose: Windows PowerShell 5.1 reads BOM-less scripts in the
# system code page, so any Chinese literal here would be garbled. Chinese text
# is built from \u escapes at runtime instead.

param([switch]$NoVoice)
$ErrorActionPreference = 'Stop'

$Catfish = Join-Path $env:USERPROFILE '.catfish'
$VenvDir = Join-Path $Catfish 'piper-venv'
$VoiceDir = Join-Path $Catfish 'piper-voices'
$Voice = 'zh_CN-huayan-medium'
$VoiceBase = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium'

# -- 0. find a Python (3.9+) -----------------------------------------------
# Prefer the interpreter Catfish already ships (the base of the hermes venv):
# it is a full python-build-standalone 3.11 with venv + pip, and needs no proxy.
function Find-Python {
    if ($env:CATFISH_PYTHON -and (Test-Path $env:CATFISH_PYTHON)) { return $env:CATFISH_PYTHON }
    $cfg = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\pyvenv.cfg'
    if (Test-Path $cfg) {
        $line = Get-Content -Encoding UTF8 $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
        if ($line) {
            $exe = Join-Path (($line -split '=', 2)[1].Trim()) 'python.exe'
            if (Test-Path $exe) { return $exe }
        }
    }
    foreach ($cand in @('py', 'python')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) {
            $pyArgs = if ($cand -eq 'py') { @('-3', '-c', 'import sys;print(sys.executable)') } else { @('-c', 'import sys;print(sys.executable)') }
            $exe = (& $cmd.Source @pyArgs 2>$null | Select-Object -First 1)
            # skip the Microsoft Store stub (WindowsApps\python.exe opens the Store)
            if ($exe -and (Test-Path $exe) -and ($exe -notmatch 'WindowsApps')) { return $exe }
        }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Host 'X  No Python found. Install Catfish Companion first (it ships one),'
    Write-Host '   or set CATFISH_PYTHON=C:\path\to\python.exe and run again.'
    exit 1
}
Write-Host "-> python = $Python"

# -- 1. venv ----------------------------------------------------------------
$Py = Join-Path $VenvDir 'Scripts\python.exe'
if (Test-Path $Py) {
    Write-Host "-> venv exists: $VenvDir"
} else {
    Write-Host "-> creating venv: $VenvDir"
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed ($LASTEXITCODE)" }
}

# -- 2. piper-tts (has a win_amd64 wheel; GPL-3.0, runs as a separate process) --
Write-Host '-> pip install piper-tts'
& $Py -m pip install --disable-pip-version-check -q --upgrade pip
& $Py -m pip install --disable-pip-version-check piper-tts
if ($LASTEXITCODE -ne 0) { throw "pip install piper-tts failed ($LASTEXITCODE)" }
$Piper = Join-Path $VenvDir 'Scripts\piper.exe'
if (-not (Test-Path $Piper)) { throw "piper.exe was not created: $Piper" }
Write-Host "OK piper: $Piper"

# -- 3. voice model -----------------------------------------------------------
# Size check guards against HuggingFace LFS pointer files (a ~130 byte text file
# saved as .onnx) -- same thresholds as the macOS script.
function Test-VoiceFile([string]$Path, [int]$MinBytes) {
    (Test-Path $Path) -and ((Get-Item $Path).Length -ge $MinBytes)
}

$Onnx = Join-Path $VoiceDir "$Voice.onnx"
$Json = Join-Path $VoiceDir "$Voice.onnx.json"
if ($NoVoice) {
    Write-Host '-> -NoVoice: skipping voice download'
} elseif ((Test-VoiceFile $Onnx 5MB) -and (Test-VoiceFile $Json 500)) {
    Write-Host '-> voice already downloaded and sane, skipping'
} else {
    New-Item -ItemType Directory -Force -Path $VoiceDir | Out-Null
    Remove-Item -Force -ErrorAction SilentlyContinue $Onnx, $Json
    Write-Host "-> downloading Chinese voice (~60MB) -> $VoiceDir"
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest 10x slower on 5.1
    Invoke-WebRequest -UseBasicParsing -Uri "$VoiceBase/$Voice.onnx" -OutFile $Onnx
    Invoke-WebRequest -UseBasicParsing -Uri "$VoiceBase/$Voice.onnx.json" -OutFile $Json
    if (-not ((Test-VoiceFile $Onnx 5MB) -and (Test-VoiceFile $Json 500))) {
        throw "voice files look wrong (LFS pointer?): $Onnx / $Json -- check network / proxy and run again"
    }
    Write-Host 'OK voice downloaded'
}

# -- 4. synthesize one sentence -------------------------------------------------
# Text goes in through a UTF-8 file + stdin redirection: piping a string from
# PowerShell 5.1 into a native exe re-encodes it with $OutputEncoding (ASCII).
if (-not $NoVoice) {
    $text = [regex]::Unescape('\u4f60\u597d\uff0c\u6211\u662f\u9cb6\u9c7c\uff0c\u5f88\u9ad8\u5174\u89c1\u5230\u4f60\u3002')
    $txt = Join-Path $env:TEMP 'catfish-piper-test.txt'
    $wav = Join-Path $env:TEMP 'catfish-piper-test.wav'
    [IO.File]::WriteAllText($txt, $text, (New-Object Text.UTF8Encoding $false))
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $p = Start-Process -FilePath $Piper -ArgumentList @('-m', "`"$Onnx`"", '-f', "`"$wav`"") `
        -RedirectStandardInput $txt -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0 -or -not (Test-Path $wav) -or (Get-Item $wav).Length -lt 1000) {
        throw "test synthesis failed (exit $($p.ExitCode))"
    }
    Write-Host "OK test synthesis: $wav ($((Get-Item $wav).Length) bytes)"
    try { (New-Object Media.SoundPlayer $wav).PlaySync() } catch { }
}

Write-Host ''
Write-Host '=== Piper TTS installed ==='
Write-Host "  piper: $Piper"
Write-Host "  voice: $Onnx"
Write-Host 'Restart Catfish Companion, then click the speaker button under a reply.'
