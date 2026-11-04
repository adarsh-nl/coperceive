# SPDX-License-Identifier: Apache-2.0
# Windows Desktop smoke test: launches app binary,
# starts local sidecar with synthetic scene fixture, and queries /api/scenes health endpoint.

param(
    [string]$AppExe = "src-tauri\target\release\coperceive-desktop.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $AppExe)) {
    if (Test-Path "src-tauri\target\debug\coperceive-desktop.exe") {
        $AppExe = "src-tauri\target\debug\coperceive-desktop.exe"
    } else {
        Write-Error "App executable not found at $AppExe"
        exit 1
    }
}

Write-Host "Using coperceive-desktop binary: $AppExe"

$FixtureRoot = (Resolve-Path "tests\fixtures\synthetic_scene").Path
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("coperceive-smoke-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
$MarkerFile = Join-Path $TempDir "test_marker.txt"
$StateDir = Join-Path $TempDir "state"

$env:COPERCEIVE_AUTOCONNECT_ROOT = $FixtureRoot
$env:COPERCEIVE_TEST_MARKER_FILE = $MarkerFile
$env:COPERCEIVE_STATE_DIR = $StateDir

if (-not $env:COPERCEIVE_PYTHON) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $env:COPERCEIVE_PYTHON = $pyCmd.Source
    }
}

$proc = Start-Process -FilePath $AppExe -PassThru

Write-Host "Waiting for auto-connect and sidecar startup (PID: $($proc.Id))..."
$timeout = 30
$elapsed = 0
while (-not (Test-Path $MarkerFile) -and $elapsed -lt $timeout) {
    if ($proc.HasExited) {
        Write-Error "App process exited prematurely with exit code $($proc.ExitCode)"
        exit 2
    }
    Start-Sleep -Milliseconds 500
    $elapsed += 0.5
}

if (-not (Test-Path $MarkerFile)) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Error "Auto-connect marker file was not created within $timeout seconds"
    exit 3
}

$lines = Get-Content $MarkerFile
$origin = $lines[0]
$token = $lines[1]

Write-Host "Auto-connected to origin: $origin"

$headers = @{ "Authorization" = "Bearer $token" }
$response = Invoke-RestMethod -Uri "$origin/api/scenes" -Headers $headers -Method Get
$sceneCount = @($response).Count
if ($sceneCount -lt 1) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Error "Expected >= 1 scene in response, got $sceneCount"
    exit 4
}

Write-Host "Verified: /api/scenes returned $sceneCount scene(s) with HTTP 200 OK."

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Windows Desktop smoke test PASSED successfully."
