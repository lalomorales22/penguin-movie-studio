# Starts Penguin's Storyboard on Windows. EXPERIMENTAL (see setup.ps1).
# Double-click "Start Storyboard.bat" in the app's folder: it runs this file.
# The app opens in your browser at http://127.0.0.1:8777 and starts the picture and video engine by itself.
# Keep this file plain ASCII: Windows PowerShell misreads other characters in a script without a marker.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Port = "8777"
if ($env:STORYBOARD_PORT) { $Port = $env:STORYBOARD_PORT }
$Url = "http://127.0.0.1:$Port"
$Py = Join-Path $Root "ComfyUI\.venv\Scripts\python.exe"

# Programs that setup installed are not on this window's PATH until Windows is asked again.
function Refresh-Path {
    $have = @($env:Path -split ";" | Where-Object { $_ })
    $listed = @([Environment]::GetEnvironmentVariable("Path", "Machine"), [Environment]::GetEnvironmentVariable("Path", "User")) -join ";"
    $usual = @((Join-Path $env:USERPROFILE ".local\bin"), (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"),
               (Join-Path $env:LOCALAPPDATA "Programs\Ollama"))
    foreach ($p in (@($listed -split ";") + $usual)) {
        if ($p -and ($have -notcontains $p)) { $have += $p }
    }
    $env:Path = $have -join ";"
}

# Already running? Then only show it.
try {
    Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/state" -TimeoutSec 2 | Out-Null
    Write-Host "Storyboard is already running - opening it in your browser."
    Start-Process $Url
    exit 0
} catch { }

# On a new computer, install what the app needs first (the engine, ffmpeg, LilyPond, Ollama).
if (-not (Test-Path -LiteralPath $Py)) {
    Write-Host "This looks like the first start on this computer. Running setup first..."
    & (Join-Path $PSScriptRoot "setup.ps1")
    if (-not (Test-Path -LiteralPath $Py)) {
        Write-Host "Setup did not finish. Fix what it reported, then double-click Start Storyboard.bat again."
        exit 1
    }
}

Refresh-Path
$env:PYTHONUTF8 = "1"   # every text file of the app is UTF-8; Windows would otherwise assume an old code page
$lily = Join-Path $Root "tools\lilypond\bin\lilypond.exe"
if ((-not $env:LILYPOND) -and (Test-Path -LiteralPath $lily)) { $env:LILYPOND = $lily }

Write-Host "Starting Penguin's Storyboard... your browser will open in a moment."
& $Py (Join-Path $Root "storyboard\app.py")
exit $LASTEXITCODE
