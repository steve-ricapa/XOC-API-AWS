param(
    [Parameter(Mandatory=$true)][ValidateSet('Build','Rebuild','Image','Tests','Smoke','Recovery','Provenance','Benchmark','BenchmarkL4','Inspect','Stop')][string]$Step,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9-]{2,40}$')][string]$RunId,
    [ValidateRange(1,3)][int]$Sample = 1
)
$ErrorActionPreference = 'Stop'
$lab = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$run = Join-Path $lab ('l6-runs\' + $RunId)
$builder = Join-Path $lab 'packaging\l6_build.py'
$python = Join-Path $lab '.venv\Scripts\python.exe'
$l4 = 'sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9'
$imageTag = 'xoc-mcp-l6-local:' + $RunId
$baseTag = 'xoc-mcp-l6-base:' + $RunId
$phase = if ($Step -eq 'BenchmarkL4') { 'benchmark-l4' } else { $Step.ToLowerInvariant() }
$phaseId = if ($Step -in @('Benchmark','BenchmarkL4')) { $phase + '-' + $Sample } else { $phase }
$name = 'xoc-l6-' + $RunId + '-' + $phaseId

function Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}
function New-Output([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite: $Path" }
}
function Write-NewJson([string]$Path, $Value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 12) + "`n")
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}
function Probe-Image([string]$Image) {
    $priorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $id = & docker image inspect $Image --format '{{.Id}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $id -match '^sha256:[a-f0-9]{64}$') { return $id }
        return $null
    } finally { $ErrorActionPreference = $priorPreference }
}
if ($Sample -ne 1 -and $Step -notin @('Benchmark','BenchmarkL4')) {
    throw '-Sample is only for the three paired benchmark repetitions.'
}
if ($Step -eq 'Build') {
    New-Output $run
    if ((Probe-Image $l4) -ne $l4) {
        throw 'Exact local L4 image absent or Docker unavailable. No pull/start-daemon/setup is authorized.'
    }
    [System.IO.Directory]::CreateDirectory($run) | Out-Null
    Write-NewJson (Join-Path $run 'run.json') @{
        schema = 'xoc-l6-run-v1'; run_id = $RunId; lab_only = $true; l4_image = $l4
        created_utc = [DateTime]::UtcNow.ToString('o'); new_run_required = $true
    }
} else {
    $markerPath = Join-Path $run 'run.json'
    if (-not (Test-Path -LiteralPath $markerPath)) { throw 'RunId must be initialized by a new Build.' }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    if ($marker.schema -ne 'xoc-l6-run-v1' -or $marker.run_id -ne $RunId -or $marker.l4_image -ne $l4) {
        throw 'L6 RunId ownership/provenance mismatch.'
    }
}
if ($Step -in @('Build','Rebuild')) {
    if ((Probe-Image $l4) -ne $l4) { throw 'Exact local L4 builder image is required.' }
    $suffix = if ($Step -eq 'Build') { 'a' } else { 'b' }
    $source = Join-Path $run ('sources-' + $suffix)
    $output = Join-Path $run ('build-' + $suffix)
    New-Output $source
    New-Output $output
    Checked $python @('-I','-B',$builder,'export','--output',$source)
    Checked $python @('-I','-B',$builder,'docker-build','--staging',$source,'--output',$output,'--image',$l4,'--name',$name)
    if ($Step -eq 'Rebuild') {
        Checked $python @('-I','-B',$builder,'compare','--first',(Join-Path $run 'build-a'),'--second',$output)
    }
    return
}
if ($Step -eq 'Image') {
    $context = Join-Path $run 'execution'
    New-Output $context
    New-Output (Join-Path $run 'image.json')
    if ((Probe-Image $l4) -ne $l4) { throw 'Exact local L4 base image is required.' }
    if (Probe-Image $imageTag) { throw 'L6 image tag already exists; choose a new RunId.' }
    if (Probe-Image $baseTag) { throw 'L6 base tag already exists; choose a new RunId.' }
    Checked $python @('-I','-B',$builder,'compare','--first',(Join-Path $run 'build-a'),'--second',(Join-Path $run 'build-b'))
    Checked $python @('-I','-B',$builder,'context','--artifacts',(Join-Path $run 'build-a'),'--output',$context)
    # The new local alias avoids confusing image config ID with registry digest.
    Checked 'docker' @('tag',$l4,$baseTag)
    Checked 'docker' @('build','--pull=false','--network','none','--platform','linux/amd64',
        '--build-arg',('L4_BASE=' + $baseTag),'-t',$imageTag,'-f',(Join-Path $context 'Dockerfile.l6'),$context)
    $imageId = Probe-Image $imageTag
    if (-not $imageId) { throw 'Built image ID unavailable.' }
    Write-NewJson (Join-Path $run 'image.json') @{
        lab_only = $true; run_id = $RunId; image_id = $imageId; image_tag = $imageTag
        l4_base_id = $l4; offline = $true; dependencies_changed = $false
    }
    return
}
if ($Step -eq 'Inspect') {
    Checked 'docker' @('image','inspect',$imageTag,'--format','{{.Id}}')
    Checked 'docker' @('ps','-a','--filter','label=io.xoc.lab=l6','--filter',('name=xoc-l6-' + $RunId + '-'),
        '--format','{{.ID}} {{.Names}} {{.Status}}')
    return
}
if ($Step -eq 'Stop') {
    # Exact persisted ID + name + labels are checked before export/finite stop.
    Checked $python @('-I','-B',$builder,'stop','--run-directory',$run)
    return
}
$savedImage = Get-Content -LiteralPath (Join-Path $run 'image.json') -Raw | ConvertFrom-Json
if ((Probe-Image $imageTag) -ne $savedImage.image_id) { throw 'Candidate image tag drift.' }
$output = Join-Path $run ('results\' + $phaseId)
New-Output $output
# Both sides of each pair use the same candidate image, budgets and population.
Checked $python @('-I','-B',$builder,'run','--image',$savedImage.image_id,'--name',$name,'--phase',$phase,'--output',$output)
