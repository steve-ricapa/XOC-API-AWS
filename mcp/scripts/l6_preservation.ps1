param(
    [string]$Output = 'reports\l6-preservation.json',
    [switch]$IncludeHistoricalGroups
)
# Read-only integrity comparisons; the sole write is a NEW report in this lab.
# Git status/HEAD equality is NOT a byte manifest of any external repository.
$ErrorActionPreference = 'Stop'
$lab = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$workspace = [System.IO.Directory]::GetParent($lab).FullName
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $lab $Output))
if (-not $outputPath.StartsWith($lab + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preservation report must remain inside XOC-MCP-LAB.'
}
if (Test-Path -LiteralPath $outputPath) { throw "Refusing to overwrite evidence: $outputPath" }
$baselinePath = Join-Path $lab 'reports\l6-baseline.json'
$baseline = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($baseline.captured_before_implementation -ne $true) { throw 'Invalid preservation baseline.' }

function File-Measure([string]$Path) {
    $absolute = [System.IO.Path]::GetFullPath($Path)
    if (-not $absolute.StartsWith($lab + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Manifest file escaped laboratory.'
    }
    $longPath = '\\?\' + $absolute
    $attributes = [System.IO.File]::GetAttributes($longPath)
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Linked input cannot establish byte preservation.'
    }
    $stream = [System.IO.File]::Open($longPath, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $size = $stream.Length
        $hash = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
        return @{ bytes = $size; sha256 = $hash }
    } finally { $stream.Dispose(); $sha.Dispose() }
}
function Hash-Text([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}
function Git-Read([string]$RepoPath, [string[]]$Arguments) {
    $lines = @(& git --no-optional-locks -C $RepoPath @Arguments)
    if ($LASTEXITCODE -ne 0) { throw "Read-only Git command failed: $RepoPath" }
    return $lines
}
$fileResults = @()
foreach ($entry in $baseline.files) {
    $path = [System.IO.Path]::GetFullPath((Join-Path $lab $entry.path))
    $observed = $null
    $failure = $null
    try { $observed = File-Measure $path } catch { $failure = $_.Exception.GetType().Name }
    $fileResults += @{
        path = $entry.path; expected_bytes = $entry.bytes; expected_sha256 = $entry.sha256.ToLowerInvariant()
        observed = $observed; error_type = $failure
        identical = ($null -ne $observed -and $observed.bytes -eq $entry.bytes -and $observed.sha256 -eq $entry.sha256)
    }
}
$repoResults = @()
# Only names already captured by the root baseline. No source/log inspection.
foreach ($entry in $baseline.repos) {
    if ($entry.repo -notmatch '^[A-Za-z0-9_-]+$') { throw 'Unsafe baseline repository name.' }
    $repoPath = [System.IO.Path]::GetFullPath((Join-Path $workspace $entry.repo))
    if ([System.IO.Directory]::GetParent($repoPath).FullName -ne $workspace) { throw 'Repository escaped workspace.' }
    $branch = (Git-Read $repoPath @('branch','--show-current')) -join "`n"
    $head = (Git-Read $repoPath @('rev-parse','HEAD')) -join "`n"
    $status = @(Git-Read $repoPath @('status','--short'))
    $repoResults += @{
        repo = $entry.repo; branch = $branch; head = $head; status = $status
        branch_identical = ($branch -ceq $entry.branch); head_identical = ($head -ceq $entry.head)
        status_identical = (($status -join "`n") -ceq (@($entry.status) -join "`n"))
        proves_all_file_hashes = $false; read_only_commands = $true
    }
}
$groupResults = @()
if ($IncludeHistoricalGroups) {
    $historical = Get-Content -LiteralPath (Join-Path $lab 'reports\l6-baseline-groups.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($group in $historical.lab_groups) {
        $groupPath = [System.IO.Path]::GetFullPath((Join-Path $lab $group.group))
        if (-not $groupPath.StartsWith($lab + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe baseline group.' }
        # Match the pre-edit baseline's PowerShell enumeration/sort exactly.
        # .NET recursive enumeration is NOT interchangeable on this tree.
        if ([System.IO.Directory]::Exists('\\?\' + $groupPath)) {
            $paths = @(Get-ChildItem -LiteralPath $groupPath -File -Recurse -Force | Sort-Object FullName | ForEach-Object { $_.FullName })
        } else { $paths = @($groupPath) }
        $rows = [System.Collections.Generic.List[string]]::new()
        [long]$bytes = 0
        foreach ($path in $paths) {
            $relative = $path.Substring($lab.Length + 1).Replace('\','/')
            # Explicit additive L6 files only; no historical file is excluded.
            if ($relative -match '^(reports/l6-[^/]+|packaging/(l6_build\.py|Dockerfile\.l6)|scripts/l6[^/]*|tests/test_l6[^/]*)$') { continue }
            $observed = File-Measure $path
            $rows.Add($relative + ':' + $observed.bytes + ':' + $observed.sha256)
            $bytes += $observed.bytes
        }
        $manifest = Hash-Text ([string]::Join("`n", $rows))
        $groupResults += @{
            group = $group.group; files = $rows.Count; bytes = $bytes; manifest_sha256 = $manifest
            expected_files = $group.files; expected_bytes = $group.bytes; expected_manifest_sha256 = $group.manifest_sha256
            identical = ($rows.Count -eq $group.files -and $bytes -eq $group.bytes -and $manifest -ceq $group.manifest_sha256)
        }
    }
}
$fileFailures = @($fileResults | Where-Object { -not $_.identical })
$repoFailures = @($repoResults | Where-Object { -not ($_.branch_identical -and $_.head_identical -and $_.status_identical) })
$groupFailures = @($groupResults | Where-Object { -not $_.identical })
$report = @{
    schema = 'xoc-l6-preservation-v1'; lab_only = $true; captured_utc = [DateTime]::UtcNow.ToString('o')
    baseline_sha256 = (File-Measure $baselinePath).sha256
    passed = ($fileFailures.Count -eq 0 -and $repoFailures.Count -eq 0 -and $groupFailures.Count -eq 0)
    saved_files = $fileResults; repositories = $repoResults; historical_groups = $groupResults
    historical_groups_checked = [bool]$IncludeHistoricalGroups
    scope_note = 'Saved laboratory files are compared byte-for-byte via SHA256. External repositories are compared only by branch, HEAD and exact status; this is not a hash of every external file.'
    manifest_is_restorable_backup = $false; foreign_repository_writes = $false
    historical_group_method = 'Baseline-compatible Get-ChildItem -File -Recurse -Force, PowerShell culture sorting; lab-relative path:bytes:lowercase-sha256 joined with LF and no final LF; only additive L6 filenames excluded. Scope is the files enumerated by the original baseline, not an assertion about unenumerated paths.'
}
$parent = [System.IO.Path]::GetDirectoryName($outputPath)
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
$encoded = [System.Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 20) + "`n")
$stream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try { $stream.Write($encoded, 0, $encoded.Length) } finally { $stream.Dispose() }
Write-Output ("Preservation: files={0}, repos={1}, groups={2}, mismatches={3}, passed={4}, report={5}" -f
    $fileResults.Count,$repoResults.Count,$groupResults.Count,($fileFailures.Count+$repoFailures.Count+$groupFailures.Count),$report.passed,$outputPath)
if (-not $report.passed) { throw 'Preservation mismatch; retained report. Do not rewrite historical files to conceal it.' }
