# SPDX-License-Identifier: Apache-2.0
# Windows Desktop smoke test: launches app binary,
# starts local sidecar with synthetic scene fixture, and queries /api/scenes health endpoint.

param(
    [string]$AppExe = "src-tauri\target\release\coperceive-desktop.exe"
)

$ErrorActionPreference = "Stop"

function Stop-AppTree {
    param([int]$RootPid)
    if ($RootPid -le 0) { return }
    $treePids = New-Object System.Collections.Generic.HashSet[int]
    [void]$treePids.Add($RootPid)
    $q = New-Object System.Collections.Generic.Queue[int]
    $q.Enqueue($RootPid)
    while ($q.Count -gt 0) {
        $p = $q.Dequeue()
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $p" -ErrorAction SilentlyContinue | ForEach-Object {
            $cid = [int]$_.ProcessId
            if ($treePids.Add($cid)) { $q.Enqueue($cid) }
        }
    }
    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & taskkill /F /T /PID $RootPid *> $null
    } catch {
    } finally {
        $ErrorActionPreference = $prevEap
    }
    foreach ($pidToStop in $treePids) {
        Stop-Process -Id $pidToStop -Force -ErrorAction SilentlyContinue
    }
    $waitDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $waitDeadline) {
        $alive = @($treePids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive.Count -eq 0) { break }
        Start-Sleep -Milliseconds 200
    }
}

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

# Normal (non-test) release launch must NOT start a loopback controller
Write-Host ""
Write-Host "--- Production Launch Controller Gate Check (Windows) ---"
$ProdStateDir = Join-Path $TempDir "prod-state"
New-Item -ItemType Directory -Path $ProdStateDir -Force | Out-Null
$ProdStdOutFile = Join-Path $TempDir "prod_stdout.log"
$ProdStdErrFile = Join-Path $TempDir "prod_stderr.log"
foreach ($varName in @(
    "COPERCEIVE_TEST_ENV",
    "COPERCEIVE_TEST_MARKER_FILE",
    "COPERCEIVE_CTRL_INFO_FILE",
    "COPERCEIVE_AUTOCONNECT_ROOT",
    "COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT",
    "COPERCEIVE_UPDATE_URL",
    "COPERCEIVE_MOCK_DIALOG_FILE",
    "COPERCEIVE_TEST_IMPORT_SOURCE"
)) {
    Remove-Item "Env:$varName" -ErrorAction SilentlyContinue
}
$env:COPERCEIVE_STATE_DIR = $ProdStateDir
$prodProc = Start-Process -FilePath $AppExe -RedirectStandardOutput $ProdStdOutFile -RedirectStandardError $ProdStdErrFile -PassThru
for ($i = 0; $i -lt 8; $i++) {
    if ($prodProc.HasExited) {
        if (Test-Path $ProdStdErrFile) { Get-Content $ProdStdErrFile }
        if (Test-Path $ProdStdOutFile) { Get-Content $ProdStdOutFile }
        Write-Error "Production launch exited prematurely with exit code $($prodProc.ExitCode)"
        exit 8
    }
    Start-Sleep -Milliseconds 500
}
$prodOutText = ""
if (Test-Path $ProdStdOutFile) { $prodOutText += (Get-Content $ProdStdOutFile -Raw) }
if (Test-Path $ProdStdErrFile) { $prodOutText += (Get-Content $ProdStdErrFile -Raw) }
if ($prodOutText -match "Loopback controller listening|\[desktop-ctrl\]") {
    Stop-AppTree $prodProc.Id
    Write-Host $prodOutText
    Write-Error "Security failure: loopback controller started during normal (non-test) launch!"
    exit 8
}
$prodPids = New-Object System.Collections.Generic.HashSet[int]
[void]$prodPids.Add($prodProc.Id)
$queue = New-Object System.Collections.Generic.Queue[int]
$queue.Enqueue($prodProc.Id)
while ($queue.Count -gt 0) {
    $parentId = $queue.Dequeue()
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" -ErrorAction SilentlyContinue | ForEach-Object {
        $childId = [int]$_.ProcessId
        if ($prodPids.Add($childId)) { $queue.Enqueue($childId) }
    }
}
$prodListeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $prodPids.Contains([int]$_.OwningProcess) })
if ($prodListeners.Count -gt 0) {
    Stop-AppTree $prodProc.Id
    $prodListeners | Format-Table -AutoSize
    Write-Error "Security failure: unexpected listening TCP socket(s) during normal launch!"
    exit 8
}
Stop-AppTree $prodProc.Id
Start-Sleep -Seconds 1
Write-Host "PROD_LAUNCH_VERIFIED: os=windows listening_ports=0 ctrl_disabled=true"

$env:COPERCEIVE_AUTOCONNECT_ROOT = $FixtureRoot
$env:COPERCEIVE_TEST_MARKER_FILE = $MarkerFile
$env:COPERCEIVE_STATE_DIR = $StateDir

$appDir = Split-Path -Parent (Resolve-Path $AppExe).Path
$pyExe = $null
$candidates = @(
    (Join-Path $appDir "resources\python\python.exe"),
    (Join-Path $appDir "resources\python\install\python.exe"),
    (Join-Path $appDir "python\python.exe")
)
foreach ($cand in $candidates) {
    if (Test-Path $cand) {
        $pyExe = (Resolve-Path $cand).Path
        break
    }
}
if (-not $pyExe) {
    Write-Error "Bundled Python interpreter not found in installed app ($appDir)!"
    exit 2
}
Write-Host "Found bundled Python: $pyExe"
& $pyExe --version
Remove-Item Env:\COPERCEIVE_PYTHON -ErrorAction SilentlyContinue

$StdOutFile = Join-Path $TempDir "app_stdout.log"
$StdErrFile = Join-Path $TempDir "app_stderr.log"

$proc = Start-Process -FilePath $AppExe -RedirectStandardOutput $StdOutFile -RedirectStandardError $StdErrFile -PassThru

Write-Host "Waiting for auto-connect and sidecar startup (PID: $($proc.Id))..."
$timeout = 90
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
    Write-Host "=== Live child processes of PID $($proc.Id) ==="
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $($proc.Id)" -ErrorAction SilentlyContinue |
        Select-Object ProcessId, Name | Format-Table -AutoSize
    Write-Host "=== Desktop app logs in LOCALAPPDATA ==="
    $desktopLogDir = Join-Path $env:LOCALAPPDATA "nl.adarsh.coperceive\logs"
    if (Test-Path $desktopLogDir) {
        Get-ChildItem -Path $desktopLogDir -Filter "*.log" -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Host "--- $($_.FullName) ---"
            Get-Content $_.FullName
        }
    }
    Stop-AppTree $proc.Id
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
$response = Invoke-RestMethod -Uri "$origin/api/scenes" -Headers $headers -Method Get -TimeoutSec 10
$sceneCount = @($response).Count
if ($sceneCount -lt 1) {
    Stop-AppTree $proc.Id
    Write-Error "Expected >= 1 scene in response, got $sceneCount"
    exit 4
}

Write-Host "Verified: /api/scenes returned $sceneCount scene(s) with HTTP 200 OK."

Write-Host "Checking GET $origin/api/health..."
$healthResp = Invoke-RestMethod -Uri "$origin/api/health" -Headers $headers -Method Get -TimeoutSec 10
if ($healthResp.status -ne "ok") {
    Stop-AppTree $proc.Id
    Write-Error "Unexpected /api/health response: $($healthResp | ConvertTo-Json -Compress)"
    exit 5
}

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

if (-not $ctrlPort -or $ctrlPort -eq 0 -or $ctrlPort -eq "0" -or -not $ctrlSecret) {
    Stop-AppTree $proc.Id
    Write-Error "Controller port/secret missing in $MarkerFile"
    exit 6
}

$ctrlHeaders = @{
    "Authorization" = "Bearer $ctrlSecret"
    "Content-Type"  = "application/json"
}

Write-Host "Capturing viewer screenshot..."
$viewerReady = $false
$waited = 0
while ($waited -lt 50) {
    try {
        $evalBody = @{ script = "Boolean(window.__COPERCEIVE_DESKTOP__)" } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/eval" -Method Post -Headers $ctrlHeaders -Body $evalBody -TimeoutSec 10
        if ($resp.result -eq $true) {
            $viewerReady = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 200
    $waited++
}
if (-not $viewerReady) {
    Stop-AppTree $proc.Id
    Write-Error "Viewer webview did not respond to controller eval probe within timeout!"
    exit 6
}
Capture-ScreenShot "viewer.png"

Write-Host "Navigating to Dataset View and capturing screenshot..."
$null = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/show-library" -Method Post -Headers $ctrlHeaders -Body "{}" -TimeoutSec 10
$evalBody = @{
    window = "main"
    script = "window.location.hash = '#/connect?tab=hpc'; true;"
} | ConvertTo-Json
$null = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/eval" -Method Post -Headers $ctrlHeaders -Body $evalBody -TimeoutSec 10

$hpcReady = $false
$waited = 0
while ($waited -lt 30) {
    try {
        $checkBody = @{
            window = "main"
            script = "Boolean(window.location.hash.includes('hpc'))"
        } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$ctrlPort/eval" -Method Post -Headers $ctrlHeaders -Body $checkBody -TimeoutSec 10
        if ($resp.result -eq $true) {
            $hpcReady = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 200
    $waited++
}
if (-not $hpcReady) {
    Stop-AppTree $proc.Id
    Write-Error "Main window did not navigate to HPC tab within timeout!"
    exit 7
}
Capture-ScreenShot "dataset_view.png"

Stop-AppTree $proc.Id
Start-Sleep -Seconds 1

Write-Host ""
Write-Host "--- First-Run Welcome Card & Demo Mode Verification (Windows) ---"
$DemoStateDir = Join-Path $TempDir "demo-state"
New-Item -ItemType Directory -Path $DemoStateDir -Force | Out-Null
$DemoMarkerFile = Join-Path $TempDir "demo_marker.txt"
$DemoStdOutFile = Join-Path $TempDir "demo_stdout.log"
$DemoStdErrFile = Join-Path $TempDir "demo_stderr.log"

Remove-Item Env:\COPERCEIVE_AUTOCONNECT_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:\COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT -ErrorAction SilentlyContinue
Remove-Item Env:\COPERCEIVE_PYTHON -ErrorAction SilentlyContinue
$env:COPERCEIVE_STATE_DIR = $DemoStateDir
$env:COPERCEIVE_TEST_MARKER_FILE = $DemoMarkerFile

Write-Host "Starting coperceive-desktop on empty state dir: $DemoStateDir..."
$demoAppProc = Start-Process -FilePath $AppExe -RedirectStandardOutput $DemoStdOutFile -RedirectStandardError $DemoStdErrFile -PassThru

$demoCtrlPort = 0
$demoCtrlSecret = ""
$elapsed = 0
while ($elapsed -lt 45) {
    if (Test-Path $DemoMarkerFile) {
        $dLines = @(Get-Content $DemoMarkerFile)
        if ($dLines.Count -ge 5 -and $dLines[3] -and $dLines[3] -ne "0" -and $dLines[4]) {
            $demoCtrlPort = $dLines[3]
            $demoCtrlSecret = $dLines[4]
            break
        }
    }
    if ($demoAppProc.HasExited) {
        if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
        if (Test-Path $DemoStdOutFile) { Get-Content $DemoStdOutFile }
        Write-Error "Demo test app process exited prematurely with code $($demoAppProc.ExitCode)"
        exit 20
    }
    Start-Sleep -Milliseconds 500
    $elapsed += 0.5
}

if (-not $demoCtrlPort -or $demoCtrlPort -eq "0" -or -not $demoCtrlSecret) {
    Stop-AppTree $demoAppProc.Id
    if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
    if (Test-Path $DemoStdOutFile) { Get-Content $DemoStdOutFile }
    Write-Error "Controller port/secret not written to $DemoMarkerFile within timeout"
    exit 20
}
Write-Host "Controller ready on port $demoCtrlPort"

$demoHeaders = @{
    "Authorization" = "Bearer $demoCtrlSecret"
    "Content-Type"  = "application/json"
}

Write-Host "Polling for first-run library location prompt on empty state dir..."
$promptReady = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $checkBody = @{
            window = "main"
            script = "Boolean(Array.from(document.querySelectorAll('button')).find(b => (b.textContent || '').includes('Use default')))"
        } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $checkBody -TimeoutSec 10
        if ($resp.result -eq $true) {
            $promptReady = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 250
}

if (-not $promptReady) {
    $pageText = "<eval failed>"
    try {
        $dumpBody = @{
            window = "main"
            script = "document.body ? document.body.innerText : '<no body>'"
        } | ConvertTo-Json
        $dumpResp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $dumpBody -TimeoutSec 10
        $pageText = $dumpResp.result
    } catch {}
    Stop-AppTree $demoAppProc.Id
    Write-Host "Main window page content: $pageText"
    if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
    Write-Error "First-run library location prompt ('Use default') did not appear within timeout!"
    exit 21
}
Write-Host "Verified: First-run library location prompt appeared. Clicking 'Use default'..."
$useDefaultBody = @{
    window = "main"
    script = "(() => { const btn = Array.from(document.querySelectorAll('button')).find(b => (b.textContent || '').includes('Use default')); if (!btn) return 'missing'; btn.click(); return 'clicked'; })()"
} | ConvertTo-Json
$useDefaultResp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $useDefaultBody -TimeoutSec 10
if ($useDefaultResp.result -ne "clicked") {
    Stop-AppTree $demoAppProc.Id
    Write-Error "Failed to click 'Use default' button: $($useDefaultResp | ConvertTo-Json -Compress)"
    exit 21
}

Write-Host "Polling for first-run Welcome Card after confirming library location (no injected seed)..."
$welcomeReady = $false
for ($i = 0; $i -lt 120; $i++) {
    try {
        $checkBody = @{
            window = "main"
            script = "Boolean(document.querySelector('.welcome-card') && document.querySelector('[data-testid=`"welcome-open-demo`"]'))"
        } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $checkBody -TimeoutSec 10
        if ($resp.result -eq $true) {
            $welcomeReady = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 250
}

if (-not $welcomeReady) {
    $pageText = "<eval failed>"
    try {
        $dumpBody = @{
            window = "main"
            script = "document.body ? document.body.innerText : '<no body>'"
        } | ConvertTo-Json
        $dumpResp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $dumpBody -TimeoutSec 10
        $pageText = $dumpResp.result
    } catch {}
    Stop-AppTree $demoAppProc.Id
    Write-Host "Main window page content: $pageText"
    if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
    Write-Error "Welcome card did not appear on empty state dir within timeout!"
    exit 21
}
Write-Host "Verified: Welcome card appeared automatically on empty first-run state dir."
Capture-ScreenShot "welcome_card.png"

Write-Host "Clicking 'Open the demo' button in Welcome Card..."
$clickBody = @{
    window = "main"
    script = "(() => { const btn = document.querySelector('[data-testid=`"welcome-open-demo`"]'); if (!btn) return 'missing'; btn.click(); return 'clicked'; })()"
} | ConvertTo-Json
$clickResp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $clickBody -TimeoutSec 10
if ($clickResp.result -ne "clicked") {
    Stop-AppTree $demoAppProc.Id
    Write-Error "Failed to click 'Open the demo' button: $($clickResp | ConvertTo-Json -Compress)"
    exit 22
}

Write-Host "Polling until demo scene is generated, opened in viewer, and renders LiDAR points, camera strip, and boxes..."
$viewerScript = "(() => { document.querySelector('section.cam-strip[data-open=`"true`"] .cam-thumb')?.scrollIntoView?.(); const title = (document.querySelector('.dataset-badge .dataset-name')?.textContent || '').trim(); const points = [...document.querySelectorAll('.agent-points')].reduce((s, el) => s + (parseInt((el.getAttribute('title') || '0').split(' ')[0], 10) || 0), 0); const cameras = [...document.querySelectorAll('section.cam-strip[data-open=`"true`"] .cam-thumb canvas')].filter(c => c.width > 0 && c.height > 0).length; const boxes = parseInt((document.querySelector('button.eye[aria-label=`"Boxes`"] .eye-meta')?.textContent || '0').trim(), 10) || 0; const notice = (document.querySelector('.webgl-missing')?.textContent || '').trim(); const hasWebglUnavailable = notice.includes('3D view unavailable') && notice.includes('Copy diagnostics'); const ready = title === 'Demo (synthetic)' && points > 0 && cameras >= 1 && boxes > 0 && !hasWebglUnavailable; return { ready, title, points, cameras, boxes, hasWebglUnavailable }; })()"
$viewerBody = @{
    window = "viewer"
    script = $viewerScript
} | ConvertTo-Json

$demoLoaded = $false
$lastViewerState = ""
for ($i = 0; $i -lt 480; $i++) {
    if ($demoAppProc.HasExited) {
        if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
        if (Test-Path $DemoStdOutFile) { Get-Content $DemoStdOutFile }
        Write-Error "App process exited while generating/opening demo!"
        exit 23
    }
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $viewerBody -TimeoutSec 10
        if ($null -ne $resp.result) {
            $lastViewerState = ($resp.result | ConvertTo-Json -Compress)
            if ($resp.result.ready -eq $true) {
                $demoLoaded = $true
                break
            }
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}

if (-not $demoLoaded) {
    Stop-AppTree $demoAppProc.Id
    Write-Host "Last viewer state: $lastViewerState"
    if (Test-Path $DemoStdOutFile) { Get-Content $DemoStdOutFile }
    if (Test-Path $DemoStdErrFile) { Get-Content $DemoStdErrFile }
    Write-Error "Demo scene did not finish loading in viewer within timeout!"
    exit 23
}
Write-Host "Verified viewer loaded demo scene: $lastViewerState"

Write-Host "Opening #prediction-output menu, selecting 'cav-b · noisy', and verifying 'synthetic predictions' evaluation view..."
$compareScript = "(() => { const runBtn = document.querySelector('#prediction-run'); const outBtn = document.querySelector('#prediction-output'); const list = document.querySelector('ul.menu-list[aria-labelledby=`"prediction-output`"]'); const curOut = (outBtn?.textContent || '').trim(); if (!curOut.includes('cav-b')) { if (!list && outBtn) { outBtn.click(); } else if (list) { const opt = [...list.querySelectorAll('button[role=`"option`"]')].find(b => (b.textContent || '').includes('cav-b')); if (opt) opt.click(); } } const predRun = (runBtn?.textContent || '').trim(); const evalHead = (document.querySelector('.eval-head')?.textContent || '').trim(); const ok = predRun.includes('synthetic predictions') && evalHead.includes('cav-b') && evalHead.includes('synthetic predictions'); return { ok, predRun, output: (outBtn?.textContent || '').trim(), evalHead }; })()"
$compareBody = @{
    window = "viewer"
    script = $compareScript
} | ConvertTo-Json

$compareOk = $false
$lastCompareState = ""
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$demoCtrlPort/eval" -Method Post -Headers $demoHeaders -Body $compareBody -TimeoutSec 10
        if ($null -ne $resp.result) {
            $lastCompareState = ($resp.result | ConvertTo-Json -Compress)
            if ($resp.result.ok -eq $true) {
                $compareOk = $true
                break
            }
        }
    } catch {}
    Start-Sleep -Milliseconds 250
}

if (-not $compareOk) {
    Stop-AppTree $demoAppProc.Id
    Write-Host "Last predictions state: $lastCompareState"
    Write-Error "Predictions/evaluation view did not show 'synthetic predictions' for selected output!"
    exit 24
}
Write-Host "Verified predictions/evaluation view: $lastCompareState"
Capture-ScreenShot "demo_viewer.png"

Stop-AppTree $demoAppProc.Id
Start-Sleep -Seconds 1

$demoOutcomeLine = ""
if (Test-Path $DemoStdOutFile) {
    $matched = Select-String -Path $DemoStdOutFile -Pattern "COPERCEIVE_DEMO_OUTCOME:" | Select-Object -Last 1
    if ($matched) {
        $demoOutcomeLine = $matched.Line
    }
}
if (-not $demoOutcomeLine) {
    if (Test-Path $DemoStdOutFile) { Get-Content $DemoStdOutFile }
    Write-Error "COPERCEIVE_DEMO_OUTCOME log line not found in app stdout!"
    exit 25
}
Write-Host $demoOutcomeLine
if ($demoOutcomeLine -notmatch 'python=(\S+)') {
    Write-Error "Could not parse python= from COPERCEIVE_DEMO_OUTCOME: $demoOutcomeLine"
    exit 25
}
$outcomePy = $Matches[1] -replace '^\\\\\?\\', ''
$normalizedAppDir = $appDir.TrimEnd('\') + '\'
if (-not $outcomePy.StartsWith($normalizedAppDir, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path $outcomePy)) {
    Write-Error "Demo did not run with the installed app's bundled Python! Expected under '$normalizedAppDir', got '$outcomePy'"
    exit 25
}
Write-Host "Verified: Demo ran with bundled Python inside installed app: $outcomePy"
Write-Host "DEMO_SMOKE_VERIFIED: os=windows viewer=$lastViewerState predictions=$lastCompareState outcome=`"$demoOutcomeLine`""

if ($env:GITHUB_STEP_SUMMARY) {
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "### Demo Mode Smoke Verification (Windows)"
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "- **Outcome**: ``$demoOutcomeLine``"
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "- **Viewer**: ``$lastViewerState``"
    Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "- **Predictions**: ``$lastCompareState``"
}

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
                # The index's cache.relative_path (sha1 of the raw root's real path).
                cacheRelative = "coperceive-cache/v2xset/10c21c9efb52/all"
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
Stop-AppTree $probeProc.Id

if (Test-Path $tempResult) {
    Write-Host "Fake-SSH dataset open completed in $($sw.ElapsedMilliseconds) ms"
} else {
    Write-Error "Timed out waiting for fake-SSH dataset open result"
    exit 26
}

Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Windows Desktop smoke test PASSED successfully."
