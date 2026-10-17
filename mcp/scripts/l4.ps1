param(
    [Parameter(Mandatory=$true)][ValidateSet('Build','Rebuild','Image','Tests','Smoke','Provenance','Benchmark','C1Tests','C1Smoke','C1Provenance','C1Benchmark','Inspect')][string]$Step,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9-]{2,40}$')][string]$RunId
)
$ErrorActionPreference = 'Stop'
$lab = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$run = Join-Path $lab ('l4-runs\' + $RunId)
$builder = Join-Path $lab 'packaging\l4_build.py'
$python = Join-Path $lab '.venv\Scripts\python.exe'
$c1 = 'sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40'
$base = 'public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80'
$baseExpected = 'sha256:3dd4ffcf67d1302b4c9808bdc65fbc8dd952debfbaf373392bbd8fb7c9856420'
$imageTag = 'xoc-mcp-l4-local:' + $RunId
$baseTag = 'xoc-mcp-l4-base:' + $RunId
$name = 'xoc-l4-' + $RunId + '-' + $Step.ToLowerInvariant()
function Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}
function New-Output([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite: $Path" }
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
function Local-Base {
    $id = Probe-Image $c1
    if ($id -eq $c1) { return $c1 }
    $id = Probe-Image $base
    if ($id -ne $baseExpected) {
        throw 'C1 and pinned AL2 base are absent. No pull/download/start-daemon is authorized by this script.'
    }
    return $base
}
if ($Step -in @('Build','Rebuild')) {
    $suffix = if ($Step -eq 'Build') { 'a' } else { 'b' }
    $source = Join-Path $run ('sources-' + $suffix)
    $output = Join-Path $run ('build-' + $suffix)
    New-Output $source
    New-Output $output
    $localBase = Local-Base
    Checked $python @('-I','-B',$builder,'export','--output',$source)
    # Binary tar stdin/stdout, no host mounts, nonroot/read-only/offline AL2 builder.
    Checked $python @('-I','-B',$builder,'docker-build','--staging',$source,'--output',$output,'--image',$localBase,'--name',$name)
    if ($Step -eq 'Rebuild') {
        Checked $python @('-I','-B',$builder,'compare','--first',(Join-Path $run 'build-a'),'--second',$output)
    }
    return
}
if ($Step -eq 'Image') {
    $context = Join-Path $run 'execution'
    New-Output $context
    # Never overwrite an existing L4 image or base tag, and never retag C1.
    if (Probe-Image $imageTag) { throw 'L4 image tag already exists; choose a new RunId.' }
    if (Probe-Image $baseTag) { throw 'L4 base tag already exists; choose a new RunId.' }
    $localBase = Local-Base
    $baseId = & docker image inspect $localBase --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0 -or $baseId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Invalid local base ID' }
    Checked $python @('-I','-B',$builder,'context','--artifacts',(Join-Path $run 'build-a'),
        '--preparation',(Join-Path $lab 'l2-runs\20260915\preparation'),'--output',$context)
    # A new local alias is needed because an image config ID is not a registry manifest digest.
    Checked 'docker' @('tag',$baseId,$baseTag)
    Checked 'docker' @('build','--pull=false','--network','none','--platform','linux/amd64',
        '--build-arg',('C1_BASE=' + $baseTag),'-t',$imageTag,'-f',(Join-Path $context 'Dockerfile.l4'),$context)
    return
}
if ($Step -eq 'Inspect') {
    Checked 'docker' @('image','inspect',$imageTag,'--format','{{.Id}}')
    Checked 'docker' @('ps','-a','--filter',('name=xoc-l4-' + $RunId),'--format','{{.ID}} {{.Names}} {{.Status}}')
    return
}
$phase = $Step.ToLowerInvariant()
$output = Join-Path $run ('results\' + $phase)
New-Output $output
$image = if ($Step.StartsWith('C1')) { $c1 } else { $imageTag }
# Keep tmpfs alive, copy evidence before exit, then remove only our exact container ID.
Checked $python @('-I','-B',$builder,'run','--image',$image,'--name',$name,'--phase',$phase,'--output',$output)
