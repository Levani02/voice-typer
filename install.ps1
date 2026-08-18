<#
.SYNOPSIS
    Sets up voice-typer on Windows: virtual environment, dependencies, API key, shortcut.

.DESCRIPTION
    Run this once after downloading the project. It leaves nothing for you to do except
    paste your ElevenLabs API key when asked.

        Right-click this file and choose "Run with PowerShell"

    or, from a PowerShell window opened in this folder:

        powershell -ExecutionPolicy Bypass -File install.ps1

.PARAMETER NonInteractive
    Never ask anything. The API key step is skipped and .env is left for you to fill in.
    Meant for automated checks, not for a normal install.

.PARAMETER SkipShortcut
    Do not put a shortcut on the desktop.
#>
param(
    [switch]$NonInteractive,
    [switch]$SkipShortcut
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$EnvFile = Join-Path $ProjectRoot '.env'

# The only version the project is built and tested against. 3.14 is deliberately absent:
# sounddevice and pynput do not publish wheels for it yet.
$SupportedVersions = @('3.13')

function Write-Step($number, $text) { Write-Host "`n[$number/6] $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "      $text" -ForegroundColor Green }
function Write-Note($text) { Write-Host "      $text" -ForegroundColor Gray }
function Write-Problem($text) { Write-Host "`n$text" -ForegroundColor Red }

function Stop-WithMessage($text) {
    Write-Problem $text
    Write-Host ''
    if (-not $NonInteractive) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}

# --------------------------------------------------------------------------- 1. Python

function Get-VersionFromInterpreter($exe) {
    try {
        $out = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return $out.Trim()
    } catch { return $null }
}

function Find-Python {
    # The launcher knows about every installed version, including ones not on PATH.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in $SupportedVersions) {
            try {
                $exe = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe) { return @{ Exe = $exe.Trim(); Version = $version } }
            } catch { }
        }
    }

    # No launcher, or none of the supported versions installed — try plain `python`.
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $exe = (Get-Command python).Source
        $version = Get-VersionFromInterpreter $exe
        if ($version -and $SupportedVersions -contains $version) {
            return @{ Exe = $exe; Version = $version }
        }
    }
    return $null
}

Write-Host 'voice-typer — setup' -ForegroundColor White
Write-Host '===================' -ForegroundColor White

Write-Step 1 'Looking for Python'
$python = Find-Python
if (-not $python) {
    Stop-WithMessage @"
No suitable Python was found.

voice-typer needs Python 3.13.
Python 3.14 does not work yet — the audio and keyboard libraries have no build for it.

Install Python 3.13 from https://www.python.org/downloads/release/python-3137/
Tick "Add python.exe to PATH" in the installer, then run this file again.
"@
}
Write-Ok "Python $($python.Version) found"

# ------------------------------------------------------------------- 2. the environment

Write-Step 2 'Creating the private environment for this app'
if (Test-Path $VenvPython) {
    Write-Note 'Already there — reusing it.'
} else {
    & $python.Exe -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage 'Could not create the environment (.venv).' }
    Write-Ok 'Created .venv'
}
if (-not (Test-Path $VenvPython)) { Stop-WithMessage 'The environment was created but has no python.exe in it.' }

# ------------------------------------------------------------------ 3. the dependencies

Write-Step 3 'Installing the software the app needs (this takes a minute or two)'
& $VenvPython -m pip install --upgrade pip --quiet --disable-pip-version-check
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt') --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage @"
Installing the dependencies failed.

The usual cause is no internet connection, or a company network blocking pypi.org.
Nothing has been broken — run this file again once the connection is back.
"@
}
Write-Ok 'All dependencies installed'

# ----------------------------------------------------------------------- 4. the API key

function Test-KeyIsFilledIn {
    # An .env with an empty key line is not configured — treat it as if it were absent,
    # or a half-finished first run would silently skip the question for ever after.
    if (-not (Test-Path $EnvFile)) { return $false }
    $line = Select-String -Path $EnvFile -Pattern '^\s*ELEVENLABS_API_KEY\s*=\s*(.+)$' |
        Select-Object -First 1
    return $null -ne $line
}

function Save-ApiKey($key) {
    # Written straight to .env, which is in .gitignore and never leaves this machine.
    # No BOM: python-dotenv reads the file as plain UTF-8.
    $template = Get-Content (Join-Path $ProjectRoot '.env.example') -Raw
    $filled = $template -replace 'ELEVENLABS_API_KEY=.*', "ELEVENLABS_API_KEY=$key"
    [System.IO.File]::WriteAllText($EnvFile, $filled, (New-Object System.Text.UTF8Encoding($false)))
}

function Read-ApiKeyFromUser {
    Write-Host ''
    Write-Host '      Paste your ElevenLabs API key and press Enter.' -ForegroundColor White
    Write-Host '      Get one at: https://elevenlabs.io/app/settings/api-keys' -ForegroundColor Gray
    Write-Host '      Nothing appears as you type or paste — that is deliberate.' -ForegroundColor Gray
    Write-Host ''
    $secure = Read-Host '      API key' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer).Trim()
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

Write-Step 4 'Setting up your ElevenLabs API key'
if (Test-KeyIsFilledIn) {
    Write-Note 'Your key is already in .env — leaving it alone.'
} elseif ($NonInteractive) {
    if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $ProjectRoot '.env.example') $EnvFile }
    Write-Note 'Created .env — put your key in it before starting the app.'
} else {
    $key = Read-ApiKeyFromUser
    if ([string]::IsNullOrWhiteSpace($key)) {
        if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $ProjectRoot '.env.example') $EnvFile }
        Write-Note 'No key entered. Created .env — open it and paste the key in later.'
    } else {
        Save-ApiKey $key
        Write-Ok "Key saved to .env ($($key.Length) characters)"
    }
}

# ----------------------------------------------------------------------- 5. verification

Write-Step 5 'Checking that everything loads'
$check = & $VenvPython -c "import elevenlabs, sounddevice, pynput, pystray, pyperclip, tkinter; print('ok')" 2>&1
if ($LASTEXITCODE -ne 0 -or $check -notmatch 'ok') {
    Stop-WithMessage @"
The app's parts installed but do not load:

$check

Run this file again. If it says the same thing, the log of what went wrong is above.
"@
}
Write-Ok 'Everything loads'

# -------------------------------------------------------------------------- 6. shortcut

Write-Step 6 'Putting a shortcut on your desktop'
if ($SkipShortcut) {
    Write-Note 'Skipped, as asked.'
} else {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'voice-typer.lnk'))
        $shortcut.TargetPath = Join-Path $ProjectRoot 'run.vbs'
        $shortcut.WorkingDirectory = $ProjectRoot
        $iconPath = Join-Path $ProjectRoot 'assets\voice-typer.ico'
        if (Test-Path $iconPath) { $shortcut.IconLocation = $iconPath }
        $shortcut.Description = 'voice-typer — speak Georgian, get typed text'
        $shortcut.Save()
        Write-Ok 'Shortcut created — look for "voice-typer" on your desktop'
    } catch {
        Write-Note "Could not create the shortcut: $($_.Exception.Message)"
        Write-Note 'Not a problem — double-click run.vbs in this folder instead.'
    }
}

# ------------------------------------------------------------------------------- finish

Write-Host ''
Write-Host 'Done. voice-typer is installed.' -ForegroundColor Green
Write-Host ''
Write-Host 'To start it:      double-click "voice-typer" on your desktop' -ForegroundColor White
Write-Host '                  (or run.vbs in this folder)' -ForegroundColor Gray
Write-Host 'To use it:        hold F9, speak Georgian, let go — the text appears' -ForegroundColor White
Write-Host '                  wherever your cursor is' -ForegroundColor Gray
Write-Host 'To start it with Windows: press Win+R, type  shell:startup  and put a' -ForegroundColor White
Write-Host '                  copy of the desktop shortcut in the folder that opens' -ForegroundColor Gray
Write-Host ''
Write-Host 'If something goes wrong, the reason is written in logs\voice_typer.log' -ForegroundColor Gray
Write-Host ''

if (-not $NonInteractive) { Read-Host 'Press Enter to close' | Out-Null }
