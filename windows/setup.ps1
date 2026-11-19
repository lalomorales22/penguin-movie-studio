# Penguin's Storyboard setup for Windows. EXPERIMENTAL.
#
# This was written on a Mac. GitHub's Windows machines run it after every change, but they have no graphics
# card, so making pictures and videos on Windows is the part nobody has tried yet.
#
# Double-click setup.bat in the app's folder: it runs this file. It installs what the app needs: git, ffmpeg,
# uv, Ollama, LilyPond, the picture and video engine (ComfyUI) with its Python parts and, if you want the app
# to write music, Claude Code. The big model files are NOT downloaded here: start the app afterwards and use
# its Models page for those. It is safe to run again: anything already installed is left alone.
#
#   setup.bat -Check          only report what is installed, change nothing
#   setup.bat -Gguf           also install the add-on that lets ComfyUI read GGUF prompt readers
#   setup.bat -Yes            answer yes to winget's licence questions
#   setup.bat -NoQuestions    ask nothing (Claude Code is then left as it is)
#   setup.bat -TorchIndex cu126 -PythonVersion 3.12     for NVIDIA cards older than the 20 series
#
# Keep this file plain ASCII: Windows PowerShell misreads other characters in a script without a marker.
param(
    [switch]$Check,
    [switch]$Gguf,
    [switch]$Yes,
    [switch]$NoQuestions,
    [string]$TorchIndex = "cu130",
    [string]$PythonVersion = "3.13"
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

# The engine version the app was built and tested with (the same one setup.command installs on a Mac).
$ComfyRepo = "https://github.com/comfyanonymous/ComfyUI.git"
$ComfyRef = "c844cb672186f187bf9455e2808821417a10d296"
if ($env:COMFY_REF) { $ComfyRef = $env:COMFY_REF }
$GgufRepo = "https://github.com/city96/ComfyUI-GGUF.git"
$GgufRef = "6ea2651e7df66d7585f6ffee804b20e92fb38b8a"
# LilyPond has no installer for Windows, only a zip. Setup unpacks it into the app's own tools folder.
$LilyVersion = "2.26.0"
$LilyUrl = "https://gitlab.com/lilypond/lilypond/-/releases/v$LilyVersion/downloads/lilypond-$LilyVersion-mingw-x86_64.zip"
$LilyExe = Join-Path $Root "tools\lilypond\bin\lilypond.exe"
$Py = Join-Path $Root "ComfyUI\.venv\Scripts\python.exe"
$script:Problems = 0

function Bold($Text) { Write-Host ""; Write-Host $Text -ForegroundColor White }
function Ok($Text) { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Warn($Text) { Write-Host "  [!]  $Text" -ForegroundColor Yellow }
function Miss($Text) { Write-Host "  [x]  $Text" -ForegroundColor Red; $script:Problems = 1 }
function Have($Command) { return [bool](Get-Command $Command -ErrorAction SilentlyContinue) }

# A program installed a moment ago is not on this window's PATH yet. Add what Windows now lists, and the folders
# the installers below use, without dropping anything this window already had.
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

function Winget-Install($Id) {
    $more = @()
    if ($Yes) { $more = @("--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity") }
    & winget install --id $Id -e --source winget @more | Out-Host
    Refresh-Path
}

# Need <command> <winget id> <what it is for>
function Need($Command, $Id, $What) {
    if (Have $Command) { Ok $What; return }
    if ($Check) { Miss "$What is not installed (winget install --id $Id -e)"; return }
    if (-not (Have "winget")) { Miss "$What is not installed, and this PC has no winget to install it with. Install it by hand, then run setup again."; return }
    Bold "Installing $What"
    Winget-Install $Id
    if (Have $Command) { Ok $What } else { Miss "$What could not be installed. Try: winget install --id $Id -e" }
}

Refresh-Path
Bold "Setting up Penguin's Storyboard on Windows (experimental)"
if ($Check) { Write-Host "  (checking only: nothing will be installed)" }

# --- This computer ---------------------------------------------------------------------------
try {
    Ok ((Get-CimInstance Win32_OperatingSystem).Caption)
    $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    if ($ramGb -ge 32) { Ok "$ramGb GB of memory" }
    else { Warn "$ramGb GB of memory. With a graphics card that cannot hold a whole model, the rest goes here; 32 GB or more is safer." }
} catch { Warn "Could not read this computer's details." }

$Nvidia = $false
if (Have "nvidia-smi") {
    # (the two options are in quotes because PowerShell would otherwise split them at their commas)
    $smi = @(& nvidia-smi "--query-gpu=name,memory.total" "--format=csv,noheader,nounits" 2>$null)
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in $smi) {
            $parts = "$line" -split ",\s*"
            if ($parts.Count -lt 2) { continue }
            $Nvidia = $true
            $gb = [math]::Round([int]$parts[1] / 1024)
            if ($gb -ge 20) { Ok "$($parts[0]) with $gb GB of graphics memory" }
            elseif ($gb -ge 10) { Warn "$($parts[0]) with $gb GB of graphics memory. The models were chosen for 24 GB: the engine will keep part of them in normal memory, which is slower. Nobody has measured this yet." }
            else { Warn "$($parts[0]) with only $gb GB of graphics memory. The models were chosen for 24 GB. It may be very slow or fail." }
        }
    }
}
if (-not $Nvidia) {
    $cards = "none found"
    try { $cards = (@(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join ", ") } catch { }
    Warn "No NVIDIA graphics card found (this PC has: $cards). This setup only knows NVIDIA cards so far. Everything will install, but the engine will not start without one."
}

try {
    $freeGb = [math]::Floor((Get-Item -LiteralPath $Root).PSDrive.Free / 1GB)
    if ($freeGb -ge 70) { Ok "$freeGb GB of free disk space" }
    else { Warn "$freeGb GB of free disk space. A complete install takes about 55 GB (models 43, writing model 7, engine and tools 5)." }
} catch { }
if ($Root.Length -gt 60) { Warn "The app's folder has a long path ($Root). Windows limits how long a path may be; a short folder such as C:\penguin-movie-studio is safer." }

# --- The tools (winget installs them) --------------------------------------------------------
if (Have "winget") { Ok "winget (the Windows package manager)" }
else { Warn "winget was not found. It comes with App Installer from the Microsoft Store. Without it, install the missing tools below by hand." }
Need "git" "Git.Git" "git"
Need "ffmpeg" "Gyan.FFmpeg" "ffmpeg (joins the clips and the music)"
Need "uv" "astral-sh.uv" "uv (sets up the engine's Python)"
Need "ollama" "Ollama.Ollama" "Ollama (runs the script-writing model on this computer)"

$runtime = Join-Path $env:WINDIR "System32\vcruntime140_1.dll"
if (Test-Path -LiteralPath $runtime) { Ok "Microsoft Visual C++ runtime (PyTorch needs it)" }
elseif ($Check) { Miss "The Microsoft Visual C++ runtime is not installed (winget install --id Microsoft.VCRedist.2015+.x64 -e)" }
elseif (Have "winget") {
    Bold "Installing the Microsoft Visual C++ runtime (PyTorch needs it)"
    Winget-Install "Microsoft.VCRedist.2015+.x64"
    if (Test-Path -LiteralPath $runtime) { Ok "Microsoft Visual C++ runtime" } else { Miss "The Microsoft Visual C++ runtime could not be installed. Get it from https://aka.ms/vs/17/release/vc_redist.x64.exe" }
}
else { Miss "The Microsoft Visual C++ runtime is not installed. Get it from https://aka.ms/vs/17/release/vc_redist.x64.exe" }

# --- LilyPond (sheet music and the notes the music is played from) ---------------------------
if ((Test-Path -LiteralPath $LilyExe) -or $env:LILYPOND -or (Have "lilypond")) { Ok "LilyPond (sheet music and the notes the music is played from)" }
elseif ($Check) { Miss "LilyPond is not installed yet (setup downloads it into the app's tools folder)" }
else {
    Bold "Downloading LilyPond $LilyVersion (46 MB) into the app's tools folder"
    $zip = Join-Path $env:TEMP "lilypond-$LilyVersion-mingw-x86_64.zip"
    $unpack = Join-Path $Root "tools\_unpacking"
    & curl.exe -L --fail --silent --show-error -o $zip $LilyUrl
    if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $zip)) {
        Remove-Item -Recurse -Force -LiteralPath $unpack -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $unpack | Out-Null
        # Windows' own tar unpacks a zip quickly (the tar that comes with git cannot, so it is named in full).
        $tar = Join-Path $env:WINDIR "System32\tar.exe"
        if (Test-Path -LiteralPath $tar) { & $tar -xf $zip -C $unpack } else { Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force }
        # The zip holds one folder (lilypond-2.26.0) with bin, lib, share ... in it: that folder becomes tools\lilypond.
        $exe = Get-ChildItem -LiteralPath $unpack -Recurse -Filter "lilypond.exe" | Select-Object -First 1
        if ($exe) {
            Remove-Item -Recurse -Force -LiteralPath (Join-Path $Root "tools\lilypond") -ErrorAction SilentlyContinue
            Move-Item -LiteralPath $exe.Directory.Parent.FullName -Destination (Join-Path $Root "tools\lilypond")
        }
        Remove-Item -Recurse -Force -LiteralPath $unpack -ErrorAction SilentlyContinue
        Remove-Item -Force -LiteralPath $zip -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $LilyExe) { Ok "LilyPond $LilyVersion" }
    else { Miss "LilyPond could not be downloaded. Get the Windows zip from https://lilypond.org/download.html and unpack it so that tools\lilypond\bin\lilypond.exe exists." }
}

# --- The picture and video engine (ComfyUI) --------------------------------------------------
if (Test-Path -LiteralPath "ComfyUI\main.py") { Ok "ComfyUI is in the ComfyUI folder" }
elseif ($Check) { Miss "ComfyUI is not installed yet" }
elseif (-not (Have "git")) { Miss "ComfyUI cannot be downloaded without git (see above)" }
else {
    Bold "Downloading ComfyUI (the picture and video engine)"
    New-Item -ItemType Directory -Force -Path "ComfyUI" | Out-Null
    Push-Location "ComfyUI"
    & git init -q
    & git remote add origin $ComfyRepo
    & git fetch -q --depth 1 origin $ComfyRef
    & git checkout -q FETCH_HEAD
    Pop-Location
    if (Test-Path -LiteralPath "ComfyUI\main.py") { Ok "ComfyUI" }
    else { Miss "ComfyUI could not be downloaded. Check the internet connection and run setup again." }
}

function Engine-Ready {
    if (-not (Test-Path -LiteralPath $Py)) { return $false }
    & $Py -c "import torch, aiohttp, av, huggingface_hub" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Engine-Says {
    $said = & $Py -c "import torch; print('torch', torch.__version__, '- graphics card:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NOT VISIBLE to PyTorch')" 2>$null
    return "$said"
}
if (Test-Path -LiteralPath "ComfyUI\main.py") {
    if (Engine-Ready) { Ok "The engine's Python parts ($(Engine-Says))" }
    elseif ($Check) { Miss "The engine's Python parts are not installed yet" }
    elseif (-not (Have "uv")) { Miss "The engine's Python parts cannot be installed without uv (see above)" }
    else {
        Bold "Installing the engine's Python parts (several gigabytes, this takes a while)"
        if (-not (Test-Path -LiteralPath $Py)) { & uv venv --python $PythonVersion "ComfyUI\.venv" | Out-Host }
        # PyTorch first, from the list that has the builds for NVIDIA cards. Left to itself Windows gets a PyTorch
        # that only works on the processor.
        & uv pip install --python $Py torch torchvision --index-url "https://download.pytorch.org/whl/$TorchIndex" | Out-Host
        & uv pip install --python $Py -r "ComfyUI\requirements.txt" "huggingface_hub>=1.4" | Out-Host
        if (Engine-Ready) { Ok "The engine's Python parts ($(Engine-Says))" }
        else { Miss "The engine's Python parts did not install. Run setup again to retry." }
    }
    if ((Engine-Ready) -and $Nvidia -and ((Engine-Says) -match "NOT VISIBLE")) {
        Warn "PyTorch does not see the NVIDIA card. Update the NVIDIA driver, then run setup again. For a card older than the 20 series: setup.bat -TorchIndex cu126 -PythonVersion 3.12 (after deleting the folder ComfyUI\.venv)."
    }
}

# --- Optional: GGUF prompt readers -----------------------------------------------------------
if (Test-Path -LiteralPath "ComfyUI\custom_nodes\ComfyUI-GGUF") { Ok "GGUF add-on for ComfyUI (ComfyUI-GGUF)" }
elseif ($Gguf -and (-not $Check) -and (Test-Path -LiteralPath "ComfyUI\main.py")) {
    Bold "Installing the GGUF add-on for ComfyUI"
    New-Item -ItemType Directory -Force -Path "ComfyUI\custom_nodes\ComfyUI-GGUF" | Out-Null
    Push-Location "ComfyUI\custom_nodes\ComfyUI-GGUF"
    & git init -q
    & git remote add origin $GgufRepo
    & git fetch -q --depth 1 origin $GgufRef
    & git checkout -q FETCH_HEAD
    Pop-Location
    & uv pip install --python $Py "gguf>=0.13.0" sentencepiece protobuf | Out-Host
    if (Test-Path -LiteralPath "ComfyUI\custom_nodes\ComfyUI-GGUF\nodes.py") { Ok "GGUF add-on" } else { Miss "The GGUF add-on could not be installed" }
}
else { Write-Host "  - GGUF add-on: not installed. Only needed if you choose a GGUF prompt reader on the Models page (setup.bat -Gguf)." }

# --- Music: Claude Code composes, a Chrome-family browser turns the score into sound ---------
if (Have "claude") {
    Ok "Claude Code $(& claude --version 2>$null | Select-Object -First 1)"
    $status = (@(& claude auth status 2>$null) -join " ")
    if ($status -match '"loggedIn":\s*true') { Ok "Claude Code is signed in" }
    elseif ($Check -or $NoQuestions) { Warn "Claude Code is not signed in. Open PowerShell, run claude once and sign in." }
    else {
        Bold "Claude Code needs you to sign in once (only for writing music)."
        Write-Host "  Claude will open now. Sign in, then type /exit to come back here."
        $answer = Read-Host "  Press Enter to sign in (or type s to skip)"
        if ($answer -ne "s") { & claude }
    }
}
elseif ($Check -or $NoQuestions) { Warn "Claude Code is not installed. Everything works without it except writing music." }
else {
    Bold "Claude Code writes the music for your movies. Everything else works without it."
    $answer = Read-Host "  Install Claude Code now? (Enter = yes, s = skip)"
    if ($answer -ne "s") {
        try { Invoke-RestMethod -UseBasicParsing -Uri "https://claude.ai/install.ps1" | Invoke-Expression } catch { Write-Host "  $_" }
        Refresh-Path
        if (Have "claude") { Ok "Claude Code installed. Run setup again, or run claude once, to sign in." }
        else { Warn "Claude Code could not be installed: https://claude.com/claude-code" }
    }
}

$browser = ""
$known = @(@("Google Chrome", "Google\Chrome\Application\chrome.exe"), @("Microsoft Edge", "Microsoft\Edge\Application\msedge.exe"),
           @("Brave", "BraveSoftware\Brave-Browser\Application\brave.exe"), @("Chromium", "Chromium\Application\chrome.exe"))
foreach ($b in $known) {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ((-not $browser) -and $base -and (Test-Path -LiteralPath (Join-Path $base $b[1]))) { $browser = $b[0] }
    }
}
if ($browser) { Ok "$browser (turns written music into an audio file)" }
else { Warn "No Chrome, Edge, Brave or Chromium found. Music cannot be turned into an audio file without one of them." }

if ($script:Problems -eq 0) {
    Bold 'All set. Double-click "Start Storyboard.bat", then open Models in the app to download the model files.'
}
else {
    Bold "Not everything is in place yet (see the [x] lines above)."
    if ($Check) { Write-Host "  Run setup.bat without -Check to install what is missing." }
}
exit $script:Problems
