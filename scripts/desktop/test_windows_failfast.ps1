# SPDX-License-Identifier: Apache-2.0
# Self-test for the Windows smoke fail-fast helpers: redaction, bounded wait,
# process-tree kill and evidence creation. Run before the real smoke test.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows_smoke_evidence.ps1")

$secret = "viewer-secret-6f4c2a1e"
$jsonSample = '{"token": "' + $secret + '"}'
$samples = @(
    "token=$secret",
    "Authorization: Bearer $secret",
    $jsonSample,
    "viewer_token=$secret",
    "--token $secret",
    "GET /viewer?token=$secret&frame=1"
)
foreach ($sample in $samples) {
    $redacted = Redact-SmokeText $sample
    if ($redacted -match [regex]::Escape($secret)) { throw "redactor leaked a token: $sample" }
    if ($redacted -notmatch '<redacted>') { throw "redactor did not mark a token: $sample" }
}

$marker = (@("http://127.0.0.1:9", $secret, "serve.log", "1234", "ctrl-$secret") -join [Environment]::NewLine)
$markerRedacted = Redact-MarkerText $marker
if ($markerRedacted -match [regex]::Escape($secret)) { throw "marker redactor leaked a token" }
if ($markerRedacted -notmatch '<redacted>') { throw "marker redactor did not mark a token" }

$outDir = if ($env:RUNNER_TEMP) { Join-Path $env:RUNNER_TEMP "windows-smoke-evidence" } else { Join-Path ([IO.Path]::GetTempPath()) "windows-smoke-evidence" }
$testTemp = Join-Path ([IO.Path]::GetTempPath()) ("coperceive-failfast-selftest-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
Set-Content -LiteralPath (Join-Path $testTemp "viewer.log") -Value (@("GET /?token=$secret", "Authorization: Bearer $secret") -join [Environment]::NewLine)
Set-Content -LiteralPath (Join-Path $testTemp "token-12345.txt") -Value $secret

$pwshExe = Join-Path $PSHOME "pwsh.exe"
if (-not (Test-Path -LiteralPath $pwshExe)) { $pwshExe = "pwsh" }
$child = Start-Process -FilePath $pwshExe -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 600") -PassThru
$watch = [System.Diagnostics.Stopwatch]::StartNew()
$finished = Wait-SmokeChild -Process $child -TimeoutSeconds 2
$watch.Stop()
if ($finished) { throw "the fake hung child exited before the bound" }
if ($watch.Elapsed.TotalSeconds -gt 8) { throw "the bounded wait took too long: $($watch.Elapsed.TotalSeconds)s" }
Stop-SmokeProcessTree -RootPid $child.Id
if (Get-Process -Id $child.Id -ErrorAction SilentlyContinue) { throw "the fake hung child survived the process-tree kill" }

$evidence = Write-SmokeEvidence -Label "fail-fast self-test" -TempDir $testTemp -OutDir $outDir
if (-not (Test-Path -LiteralPath $evidence -PathType Leaf)) { throw "evidence file was not written" }
$evidenceText = Get-Content -LiteralPath $evidence -Raw
if ($evidenceText -match [regex]::Escape($secret)) { throw "evidence leaked a token" }
if ($evidenceText -notmatch '<redacted>') { throw "evidence did not contain a redaction marker" }
Remove-Item -LiteralPath $testTemp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "WINDOWS_FAILFAST_SELFTEST_OK evidence=$evidence"
