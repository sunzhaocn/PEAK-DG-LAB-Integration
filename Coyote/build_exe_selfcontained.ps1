$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Exe,

        [Parameter(ValueFromRemainingArguments=$true)]
        [string[]]$Arguments
    )

    & $Exe @Arguments
    $Code = $LASTEXITCODE

    if ($null -eq $Code) {
        $Code = 0
    }

    if ($Code -ne 0) {
        throw "Command failed with exit code $Code : $Exe $($Arguments -join ' ')"
    }
}

function Copy-DirIfExists {
    param(
        [string]$Source,
        [string]$Target
    )

    if (-not (Test-Path $Source)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    Get-ChildItem -LiteralPath $Source -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item `
                -LiteralPath $_.FullName `
                -Destination $Target `
                -Recurse `
                -Force
        }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Coyote Windows x64 Portable Builder - Self Contained" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# 1. Find Python
# ------------------------------------------------------------

$PythonCandidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)

$Python = $null

foreach ($Candidate in $PythonCandidates) {
    if (Test-Path $Candidate) {
        $Python = $Candidate
        break
    }
}

if (-not $Python) {
    $Cmd = Get-Command python.exe -ErrorAction SilentlyContinue

    if ($Cmd) {
        $Python = $Cmd.Source
    }
}

if (-not $Python) {
    throw "Python 3.12+ was not found."
}

Write-Host "[1/9] Python: $Python" -ForegroundColor Green
Invoke-Native $Python "--version"

# ------------------------------------------------------------
# 2. Verify actual project files only
# ------------------------------------------------------------

$Required = @(
    "src\Coyote\main.py",
    "src\Coyote\backend.py",
    "src\Coyote\ui_qt.py",
    "src\Coyote\i18n.py",
    "src\Coyote\Coyote.csproj",
    "dglab-websocket-server-main"
)

foreach ($RelativePath in $Required) {
    $Full = Join-Path $Root $RelativePath

    if (-not (Test-Path $Full)) {
        throw "Missing project path: $RelativePath"
    }
}

Write-Host "[2/9] Project structure OK" -ForegroundColor Green

# ------------------------------------------------------------
# 2.5. Build the PEAK Coyote.dll from the current Plugin.cs
# ------------------------------------------------------------

$DotNetCommand = Get-Command dotnet.exe -ErrorAction SilentlyContinue

if (-not $DotNetCommand) {
    $DotNetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
}

if (-not $DotNetCommand) {
    throw ".NET SDK was not found. Install the SDK required by global.json, then rebuild so plugin\Coyote.dll contains the current Plugin.cs changes."
}

$DotNet = $DotNetCommand.Source
$PluginProject = Join-Path $Root "src\Coyote\Coyote.csproj"

Write-Host "[2.5/9] Building Coyote.dll: $DotNet" -ForegroundColor Yellow
Invoke-Native `
    $DotNet `
    "build" `
    $PluginProject `
    "-c" `
    "Release" `
    "-p:DeployModFiles=false" `
    "-p:RunThunderPipePackAfterBuild=false"

Write-Host "      Coyote.dll build completed." -ForegroundColor Green

# ------------------------------------------------------------
# 3. Install build/runtime dependencies
# ------------------------------------------------------------

Write-Host "[3/9] Installing dependencies..." -ForegroundColor Yellow

Invoke-Native $Python "-m" "pip" "install" "--upgrade" "pip"

Invoke-Native `
    $Python `
    "-m" `
    "pip" `
    "install" `
    "PySide6" `
    "Pillow" `
    "qrcode" `
    "websocket-client" `
    "pyinstaller>=6.15" `
    "pyinstaller-hooks-contrib"

# ------------------------------------------------------------
# 4. Optional icon
# ------------------------------------------------------------

Write-Host "[4/9] Preparing optional icon..." -ForegroundColor Yellow

$IconPng = Join-Path $Root "icon.png"
$IconIco = Join-Path $Root "icon.ico"

if (Test-Path $IconPng) {
    $IconScript = Join-Path $env:TEMP "coyote_make_icon.py"

    @'
from pathlib import Path
from PIL import Image
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

with Image.open(src) as im:
    im = im.convert("RGBA")
    im.save(
        dst,
        format="ICO",
        sizes=[
            (16,16),
            (24,24),
            (32,32),
            (48,48),
            (64,64),
            (128,128),
            (256,256),
        ],
    )
'@ | Set-Content -LiteralPath $IconScript -Encoding UTF8

    Invoke-Native $Python $IconScript $IconPng $IconIco

    Remove-Item $IconScript -Force -ErrorAction SilentlyContinue
    Write-Host "      icon.ico generated." -ForegroundColor Green
}
else {
    Write-Host "      icon.png not found; default EXE icon will be used." -ForegroundColor DarkYellow
}

# ------------------------------------------------------------
# 5. Generate temporary PyInstaller spec automatically
# ------------------------------------------------------------

Write-Host "[5/9] Generating temporary PyInstaller spec..." -ForegroundColor Yellow

$Spec = Join-Path $Root ".Coyote.generated.spec"

$IconLiteral = "None"

if (Test-Path $IconIco) {
    $EscapedIcon = $IconIco.Replace("\", "\\")
    $IconLiteral = "r'$EscapedIcon'"
}

$EscapedRoot = $Root.Replace("\", "\\")
$EscapedSrc = (Join-Path $Root "src\Coyote").Replace("\", "\\")
$EscapedEntry = (Join-Path $Root "src\Coyote\main.py").Replace("\", "\\")

$SpecText = @"
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("qrcode")
hiddenimports += collect_submodules("websocket")

a = Analysis(
    [r'$EscapedEntry'],
    pathex=[r'$EscapedSrc'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Coyote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=$IconLiteral,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Coyote',
)
"@

[System.IO.File]::WriteAllText(
    $Spec,
    $SpecText,
    [System.Text.UTF8Encoding]::new($false)
)

# ------------------------------------------------------------
# 6. Clean build
# ------------------------------------------------------------

Write-Host "[6/9] Cleaning old build..." -ForegroundColor Yellow

$BuildDir = Join-Path $Root "build"
$DistDir = Join-Path $Root "dist"

if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}

if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}

# ------------------------------------------------------------
# 7. PyInstaller
# ------------------------------------------------------------

Write-Host "[7/9] Running PyInstaller..." -ForegroundColor Yellow

try {
    Invoke-Native `
        $Python `
        "-m" `
        "PyInstaller" `
        "--noconfirm" `
        "--clean" `
        $Spec
}
finally {
    Remove-Item $Spec -Force -ErrorAction SilentlyContinue
}

$AppDir = Join-Path $Root "dist\Coyote"
$Exe = Join-Path $AppDir "Coyote.exe"

if (-not (Test-Path $Exe)) {
    throw "Coyote.exe was not generated."
}

Write-Host "      EXE generated." -ForegroundColor Green

# ------------------------------------------------------------
# 8. Copy runtime/editable resources
# ------------------------------------------------------------

Write-Host "[8/9] Copying runtime resources..." -ForegroundColor Yellow

Copy-DirIfExists `
    (Join-Path $Root "dglab-websocket-server-main") `
    (Join-Path $AppDir "dglab-websocket-server-main")

Copy-DirIfExists `
    (Join-Path $Root "assets") `
    (Join-Path $AppDir "assets")

Copy-DirIfExists `
    (Join-Path $Root "custom_rules") `
    (Join-Path $AppDir "custom_rules")

Copy-DirIfExists `
    (Join-Path $Root "src\Coyote\language") `
    (Join-Path $AppDir "src\Coyote\language")

Copy-DirIfExists `
    (Join-Path $Root "src\Coyote\md") `
    (Join-Path $AppDir "src\Coyote\md")

# Backward-compat docs directory, if the user still has files there.
Copy-DirIfExists `
    (Join-Path $Root "docs") `
    (Join-Path $AppDir "docs")

New-Item `
    -ItemType Directory `
    -Force `
    -Path (Join-Path $AppDir "logs") `
    | Out-Null

New-Item `
    -ItemType Directory `
    -Force `
    -Path (Join-Path $AppDir "plugin") `
    | Out-Null

# Copy newest compiled Coyote.dll
$DllRoots = @(
    (Join-Path $Root "artifacts\bin\Coyote"),
    (Join-Path $Root "src\Coyote\bin")
)

$Dlls = @()

foreach ($DllRoot in $DllRoots) {
    if (Test-Path $DllRoot) {
        $Dlls += Get-ChildItem `
            -Path $DllRoot `
            -Filter "Coyote.dll" `
            -Recurse `
            -File `
            -ErrorAction SilentlyContinue
    }
}

$NewestDll = $Dlls |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($NewestDll) {
    Copy-Item `
        -LiteralPath $NewestDll.FullName `
        -Destination (Join-Path $AppDir "plugin\Coyote.dll") `
        -Force

    Write-Host "      Plugin copied: $($NewestDll.FullName)" -ForegroundColor Green
}
else {
    Write-Host "      WARNING: no compiled Coyote.dll found." -ForegroundColor DarkYellow
}

# ------------------------------------------------------------
# 9. Create portable ZIP
# ------------------------------------------------------------

Write-Host "[9/9] Creating portable ZIP..." -ForegroundColor Yellow

$ReleaseDir = Join-Path $Root "release"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $ReleaseDir `
    | Out-Null

$ReleaseZip = Join-Path $ReleaseDir "Coyote_Windows_x64_Portable.zip"

if (Test-Path $ReleaseZip) {
    Remove-Item $ReleaseZip -Force
}

Compress-Archive `
    -Path (Join-Path $AppDir "*") `
    -DestinationPath $ReleaseZip `
    -CompressionLevel Optimal

if (-not (Test-Path $ReleaseZip)) {
    throw "Portable ZIP was not generated."
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " BUILD SUCCESS" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "EXE : $Exe"
Write-Host "ZIP : $ReleaseZip"
Write-Host ""

exit 0
