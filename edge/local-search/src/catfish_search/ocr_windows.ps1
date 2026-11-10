# catfish 识字 (Windows, 11/10): 系统自带的 Windows.Media.Ocr, 由 ocr.py 常驻调用。
# 从标准输入一行读一个图片路径, 每张输出一行 JSON: {"path","ok","text"} 或 {"path","ok":false,"error"}。
# 只认中文识别 (zh-Hans): 没装中文的「光学字符识别」语言功能就每行回 no_zh_ocr ——
# 拿英文识别读中文只会是乱码, 不如明说。中文版 Windows 一般随显示语言自带。
# 用 Windows PowerShell 5.1 (系统自带, 能直接用 WinRT 类型)。
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Emit($obj) {
    [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

$engine = $null
$initError = $null
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
    $script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    foreach ($tag in @('zh-Hans-CN', 'zh-Hans', 'zh-CN')) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($tag))
        if ($engine) { break }
    }
    if (-not $engine) { $initError = 'no_zh_ocr' }
} catch {
    $initError = 'no_winrt'
}

function Await($op, [Type]$type) {
    $task = $script:asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(-1)
    return $task.Result
}

while ($null -ne ($line = [Console]::In.ReadLine())) {
    $path = $line.Trim()
    if (-not $path) { continue }
    if ($initError) { Emit @{ path = $path; ok = $false; error = $initError }; continue }
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $text = ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
            Emit @{ path = $path; ok = $true; text = $text }
        } finally {
            $stream.Dispose()
        }
    } catch {
        Emit @{ path = $path; ok = $false; error = "$($_.Exception.Message)" }
    }
}
