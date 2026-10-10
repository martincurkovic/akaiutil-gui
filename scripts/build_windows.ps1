# build_windows.ps1 - builds the akaiutil-GUI Windows executable
# Can be run from anywhere. Handles uv sync, and fetching the akaiutil.exe it bundles.
# If PowerShell blocks running scripts, run once as your own user:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"

$AppName = "akaiutil-GUI"
$RootDir = Split-Path -Parent $PSScriptRoot
$SrcDir  = Join-Path $RootDir "src"

Set-Location $RootDir
Write-Host "Syncing environment with uv..."
uv sync
uv pip install pip
if ($LASTEXITCODE -ne 0) {
        Write-Host "uv sync failed - fix the error before continuing"
        exit 1
    }

# the app contains akaiutil (the official prebuilt Windows exe, checksum-verified)
Write-Host "Fetching akaiutil..."
uv run python scripts/get_akaiutil.py
if ($LASTEXITCODE -ne 0) {
        Write-Host "Fetching akaiutil failed - fix the error above before continuing."
        exit 1
    }

Set-Location $SrcDir

# derive version from the nearest git tag - falls back to a dev
# placeholder if no tags exist yet
$RawTag = git -C $RootDir describe --tags --abbrev=0 2>$null
if (-not $RawTag) {
    $Version = "0.0.0-dev"
} else {
    $Version = $RawTag -replace '^v', ''
}
Write-Host "Building version: $Version"
"APP_VERSION = `"$Version`"" | Set-Content (Join-Path $SrcDir "akaiutil_gui\_version.py")

# Windows --product-version needs EXACTLY 4 numeric parts, no suffix
# allowed (eg "0.0.0-dev" is invalid) - strip any non-numeric suffix,
# then pad or trim to exactly 4 parts
$NumericVersion = $Version -replace '[^0-9.].*$', ''
$Parts = $NumericVersion.Split('.')
while ($Parts.Count -lt 4) {
    $Parts += "0"
}
$ProductVersion = ($Parts[0..3] -join '.')
Write-Host "Windows product version: $ProductVersion"

if (-not (Test-Path "pysidedeploy.spec")) {
    Write-Host "No spec file found - generating one with our known-good settings..."

    uv run pyside6-deploy --init main.py

    (Get-Content pysidedeploy.spec) -replace '^title = .*', "title = $AppName" | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^icon = .*', 'icon = ../assets/icon/icon_512x512.png' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^mode = .*', 'mode = standalone' | Set-Content pysidedeploy.spec

    Write-Host "Spec generated and configured."
}

# Data the app reads at run time, paths relative to src\ (where this runs), as SRC=DEST inside the app:
#   the stylesheet template, icons and notices, the licence, and the akaiutil exe (found next to the app's exe)
$DataArgs = "--include-data-dir=akaiutil_gui/ui/icons=akaiutil_gui/ui/icons --include-data-files=akaiutil_gui/ui/style.qss.template=akaiutil_gui/ui/style.qss.template --include-data-dir=akaiutil_gui/ui/help=akaiutil_gui/ui/help --include-data-files=../LICENSE=LICENSE --include-data-files=../build/bin/akaiutil.exe=bin/akaiutil.exe"

# extra_args is regenerated on EVERY run (not just first-time setup),
# specifically so the version stays current even on an existing spec
(Get-Content pysidedeploy.spec) -replace '^extra_args = .*', "extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --windows-console-mode=disable --windows-product-name=$AppName --product-version=$ProductVersion $DataArgs" | Set-Content pysidedeploy.spec

Write-Host "Building $AppName..."
uv run pyside6-deploy -c pysidedeploy.spec
if ($LASTEXITCODE -ne 0) {
        Write-Host "pyside6-deploy failed - fix the error above before continuing."
        exit 1
    }
Write-Host ""
Write-Host "Done - check the output above for exactly where the build landed."
