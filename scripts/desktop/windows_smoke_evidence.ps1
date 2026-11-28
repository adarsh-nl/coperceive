# SPDX-License-Identifier: Apache-2.0
# Evidence and bounded-wait helpers for the Windows desktop smoke test.
#
# The smoke step runs on a hosted Windows runner. A silent HTTP endpoint or an
# installer that never exits must not consume the whole job: callers wait with
# Wait-SmokeChild, then call Write-SmokeEvidence and Stop-SmokeProcessTree.
# Everything written to the evidence directory is redacted here before upload.

function Redact-SmokeText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return "" }
    $redacted = [regex]::Replace($Text, '(?i)(Authorization\s*:\s*(?:Bearer\s+)?)[^\s"'']+', '$1<redacted>')
    $redacted = [regex]::Replace($redacted, '(?i)((?:viewer[_-]?)?token["'']?\s*[=:]\s*["'']?)[^\s"'',;&}]+', '$1<redacted>')
    $redacted = [regex]::Replace($redacted, '(?i)(--token\s+)[^\s"'']+', '$1<redacted>')
    $redacted = [regex]::Replace($redacted, '(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', '$1<redacted>')
    return $redacted
}

function Redact-MarkerText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return "" }
    $lines = $Text -split "\r?\n"
    if ($lines.Count -ge 2) { $lines[1] = '<redacted>' }
    if ($lines.Count -ge 5) { $lines[4] = '<redacted>' }
    return Redact-SmokeText ($lines -join [Environment]::NewLine)
}

function Get-SmokeFileText {
    param([string]$Path)
    $leaf = Split-Path -Leaf $Path
    if ($leaf -match '(?i)^token.*\.txt$') { return "<redacted>" }
    try {
        $text = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    } catch {
        return "<unreadable: $($_.Exception.Message)>"
    }
    if ((Split-Path -Leaf $Path) -match 'marker') { return Redact-MarkerText $text }
    return Redact-SmokeText $text
}

function Add-SmokeEvidenceFile {
    param(
        [System.Text.StringBuilder]$Builder,
        [string]$Path,
        [switch]$Marker
    )
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    [void]$Builder.AppendLine("===== FILE: $Path =====")
    $text = if ($Marker) { Redact-MarkerText (Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue) } else { Get-SmokeFileText $Path }
    if ($text.Length -gt 200000) { $text = $text.Substring(0, 200000) + [Environment]::NewLine + "<truncated>" }
    [void]$Builder.AppendLine($text)
}

function Write-SmokeEvidence {
    param(
        [string]$Label = "smoke failure",
        [string]$AppExe = "",
        [string]$InstallDir = "",
        [string]$TempDir = "",
        [string]$OutDir = ""
    )
    if (-not $OutDir) {
        if ($env:RUNNER_TEMP) { $OutDir = Join-Path $env:RUNNER_TEMP "windows-smoke-evidence" }
        else { $OutDir = Join-Path ([IO.Path]::GetTempPath()) "windows-smoke-evidence" }
    }
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $target = Join-Path $OutDir ("evidence-" + $stamp + ".txt")
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.AppendLine("Windows desktop smoke evidence")
    [void]$builder.AppendLine("label: $Label")
    [void]$builder.AppendLine("time: $((Get-Date).ToUniversalTime().ToString("o"))")
    [void]$builder.AppendLine("app: $AppExe")
    [void]$builder.AppendLine("install dir: $InstallDir")

    [void]$builder.AppendLine("")
    [void]$builder.AppendLine("===== PROCESS COMMAND LINES =====")
    $processText = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '(?i)coperceive|python|node|nsis|uninstall' } |
        Select-Object ProcessId, ParentProcessId, Name, CommandLine |
        Format-List | Out-String
    [void]$builder.AppendLine((Redact-SmokeText $processText))

    [void]$builder.AppendLine("===== PROCESSES =====")
    $processTable = Get-Process -ErrorAction SilentlyContinue |
        Sort-Object CPU -Descending | Select-Object -First 60 Id, ProcessName, CPU, WorkingSet64 |
        Format-Table -AutoSize | Out-String
    [void]$builder.AppendLine($processTable)

    [void]$builder.AppendLine("===== LISTENING SOCKETS =====")
    $sockets = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalAddress, LocalPort, OwningProcess, State |
        Format-Table -AutoSize | Out-String
    [void]$builder.AppendLine($sockets)

    if ($InstallDir -and (Test-Path -LiteralPath $InstallDir -PathType Container)) {
        [void]$builder.AppendLine("===== INSTALL DIR FILES =====")
        $installFiles = Get-ChildItem -LiteralPath $InstallDir -File -ErrorAction SilentlyContinue |
            Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize | Out-String
        [void]$builder.AppendLine($installFiles)
    }

    $files = @()
    if ($TempDir -and (Test-Path -LiteralPath $TempDir -PathType Container)) {
        $files += Get-ChildItem -LiteralPath $TempDir -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { @('.log', '.txt', '.json') -contains $_.Extension }
    }
    $tempRoot = [IO.Path]::GetTempPath()
    if ($tempRoot -and (Test-Path -LiteralPath $tempRoot -PathType Container)) {
        $files += Get-ChildItem -Path $tempRoot -Directory -Filter "coperceive-*" -ErrorAction SilentlyContinue |
            ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -File -Recurse -ErrorAction SilentlyContinue } |
            Where-Object { @('.log', '.txt', '.json') -contains $_.Extension }
        $files += Get-ChildItem -Path $tempRoot -Filter "serve-*.log" -File -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 20
    }
    $localLogs = Join-Path $env:LOCALAPPDATA "nl.adarsh.coperceive\logs"
    if ($localLogs -and (Test-Path -LiteralPath $localLogs -PathType Container)) {
        $files += Get-ChildItem -LiteralPath $localLogs -File -Filter "*.log" -ErrorAction SilentlyContinue
    }
    foreach ($file in ($files | Sort-Object FullName -Unique)) {
        Add-SmokeEvidenceFile -Builder $builder -Path $file.FullName -Marker:(($file.Name -match 'marker'))
    }

    $text = $builder.ToString()
    Set-Content -LiteralPath $target -Value $text -Encoding utf8
    Write-Host "Windows smoke evidence written to $target"
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "### Windows smoke evidence"
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ("**" + $Label + "**")
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "<pre>"
        $summary = $text
        if ($summary.Length -gt 12000) { $summary = $summary.Substring(0, 12000) + [Environment]::NewLine + "<truncated>" }
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ([System.Net.WebUtility]::HtmlEncode($summary))
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "</pre>"
    }
    return $target
}

function Stop-SmokeProcessTree {
    param([int]$RootPid)
    if ($RootPid -le 0) { return }
    try { & taskkill.exe /F /T /PID $RootPid *> $null } catch { }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-Process -Id $RootPid -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 100
    }
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Wait-SmokeChild {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 120
    )
    if ($null -eq $Process) { return $false }
    try {
        return $Process.WaitForExit($TimeoutSeconds * 1000)
    } catch {
        return $false
    }
}
