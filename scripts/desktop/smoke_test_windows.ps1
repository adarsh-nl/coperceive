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

$StdOutFile = Join-Path $TempDir "app_stdout.log"
$StdErrFile = Join-Path $TempDir "app_stderr.log"

$proc = Start-Process -FilePath $AppExe -RedirectStandardOutput $StdOutFile -RedirectStandardError $StdErrFile -PassThru

Write-Host "Waiting for auto-connect and sidecar startup (PID: $($proc.Id))..."
$timeout = 45
$elapsed = 0
while (-not (Test-Path $MarkerFile) -and $elapsed -lt $timeout) {
    if ($proc.HasExited) {
        Write-Host "=== App stderr ==="
        if (Test-Path $StdErrFile) { Get-Content $StdErrFile }
        Write-Host "=== App stdout ==="
        if (Test-Path $StdOutFile) { Get-Content $StdOutFile }
        Write-Error "App process exited prematurely with exit code $($proc.ExitCode)"
        exit 2
    }
    Start-Sleep -Milliseconds 500
    $elapsed += 0.5
}

if (-not (Test-Path $MarkerFile)) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Write-Host "=== App stderr ==="
    if (Test-Path $StdErrFile) { Get-Content $StdErrFile }
    Write-Host "=== App stdout ==="
    if (Test-Path $StdOutFile) { Get-Content $StdOutFile }
    Write-Host "=== Sidecar logs in temp ==="
    Get-ChildItem -Path ([System.IO.Path]::GetTempPath()) -Filter "serve-*.log" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "--- $($_.FullName) ---"
        Get-Content $_.FullName
    }
    Write-Error "Auto-connect marker file was not created within $timeout seconds"
    exit 3
}

$lines = Get-Content $MarkerFile
$origin = $lines[0]
$token = $lines[1]
$sidecarLog = if ($lines.Count -ge 3) { $lines[2] } else { "" }
$ctrlPort = if ($lines.Count -ge 4) { $lines[3] } else { 0 }
$ctrlSecret = if ($lines.Count -ge 5) { $lines[4] } else { "" }

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

$ScreenshotDir = if ($env:RUNNER_TEMP) { Join-Path $env:RUNNER_TEMP "e2e-shots" } else { Join-Path $env:TEMP "e2e-shots" }
New-Item -ItemType Directory -Path $ScreenshotDir -Force | Out-Null

function Capture-ScreenShot([string]$filename) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $dest = Join-Path $ScreenshotDir $filename
        $bitmap.Save($dest, [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()
        Write-Host "Captured screenshot: $dest"
    } catch {
        Write-Warning "Screenshot capture failed: $_"
    }
}

Write-Host "Capturing viewer screenshot..."
Start-Sleep -Seconds 2
Capture-ScreenShot "viewer.png"

if ($ctrlPort -ne 0 -and $ctrlSecret) {
    $headers = @{
        "Authorization" = "Bearer $ctrlSecret"
        "Content-Type"  = "application/json"
    }

    Write-Host "Showing Library and capturing screenshot..."
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/show-library" -Method Post -Headers $headers -Body "{}"
        Start-Sleep -Seconds 2
        Capture-ScreenShot "library.png"
    } catch {
        Write-Warning "show-library request failed: $_"
    }

    Write-Host "Navigating to Dataset View and capturing screenshot..."
    try {
        $evalBody = @{
            script = "window.location.hash = '#/connect?tab=hpc';"
        } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/eval" -Method Post -Headers $headers -Body $evalBody
        Start-Sleep -Seconds 2
        Capture-ScreenShot "dataset_view.png"
    } catch {
        Write-Warning "eval dataset view request failed: $_"
    }
}

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

# Measure fake-SSH dataset open time
Write-Host "Measuring fake-SSH dataset open time..."
$fakeSshPath = (Resolve-Path "src-tauri\tests\fixtures\hpc\fake_ssh.sh").Path
$clusterFixture = (Resolve-Path "src-tauri\tests\fixtures\hpc\cluster-a.json").Path
$openPayload = @{
    server = $clusterFixture
    steps = @(
        @{
            command = "dataset_open"
            args = @{
                profileId = "cluster-a"
                adapter = "v2xset"
                rawRoot = "/data/v2xset"
            }
        }
    )
}
$tempRequest = Join-Path $TempDir "open_request.json"
$tempResult = Join-Path $TempDir "open_result.json"
$openPayload | ConvertTo-Json -Depth 5 | Set-Content -Path $tempRequest

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$probeProc = Start-Process -FilePath $AppExe -ArgumentList "--dry-run" -Environment @{
    COPERCEIVE_TEST_ENV = "1"
    COPERCEIVE_SSH_BIN = $fakeSshPath
    COPERCEIVE_TEST_DATASET_FLOW_REQUEST = $tempRequest
    COPERCEIVE_TEST_DATASET_FLOW_RESULT = $tempResult
    COPERCEIVE_STATE_DIR = $StateDir
} -PassThru -NoNewWindow

$waited = 0
while (-not (Test-Path $tempResult) -and $waited -lt 100) {
    Start-Sleep -Milliseconds 200
    $waited++
}
$sw.Stop()
Stop-Process -Id $probeProc.Id -Force -ErrorAction SilentlyContinue

if (Test-Path $tempResult) {
    Write-Host "Fake-SSH dataset open completed in $($sw.ElapsedMilliseconds) ms"
} else {
    Write-Warning "Timed out waiting for fake-SSH dataset open result"
}

Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Windows Desktop smoke test PASSED successfully."
