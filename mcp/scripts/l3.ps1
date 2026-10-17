param(
    [Parameter(Mandatory=$true)][ValidateSet('OriginalTests','OriginalBenchmark','Build','Rebuild','Image','Tests','Smoke','Provenance','Benchmark','Profile','Inspect')][string]$Step,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9-]{2,40}$')][string]$RunId
)
$ErrorActionPreference = 'Stop'
$lab = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$run = Join-Path $lab ('l3-runs\' + $RunId)
$packaging = Join-Path $lab 'packaging'
$target = Get-Content -Raw -LiteralPath (Join-Path $packaging 'linux-target.json') | ConvertFrom-Json
$imageTag = 'xoc-mcp-l3-local:' + $RunId
$name = 'xoc-l3-' + $RunId + '-' + $Step.ToLowerInvariant()
$limits = @('--platform','linux/amd64','--memory','512m','--cpus','2','--pids-limit','128','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges')
function Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}
function New-Output([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite: $Path" }
}
if ($Step -in @('Build','Rebuild')) {
    $suffix = if ($Step -eq 'Build') { 'a' } else { 'b' }
    $sources = Join-Path $run ('sources-' + $suffix)
    $output = Join-Path $run ('build-' + $suffix)
    New-Output $sources
    New-Output $output
    $backend = (Resolve-Path -LiteralPath (Join-Path $lab '..\XOC-API-AWS')).Path
    Checked 'python' @('-B',(Join-Path $packaging 'build_artifacts.py'),'export','--backend',$backend,'--output',$sources)
    Checked 'docker' (@('run','--rm','--name',$name) + $limits + @('--network','none','--read-only',
        '--mount',"type=bind,source=$packaging,target=/inputs,readonly",'--mount',"type=bind,source=$sources,target=/sources,readonly",
        '--mount',"type=bind,source=$run,target=/output",'--entrypoint','/var/lang/bin/python',$target.image,
        '-I','-B','/inputs/build_artifacts.py','build','--staging','/sources','--output',('/output/build-' + $suffix),
        '--runtime-lock','/inputs/requirements-linux-runtime.lock.txt','--test-lock','/inputs/requirements-linux-test.lock.txt'))
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $output 'artifact-manifest.json') | ConvertFrom-Json
    if ($manifest.artifacts[0].sha256 -ne 'df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41') { throw 'STOP: canonical core changed' }
    if ($Step -eq 'Rebuild') {
        $a = Get-Content -Raw -LiteralPath (Join-Path $run 'build-a\artifact-manifest.json')
        $b = Get-Content -Raw -LiteralPath (Join-Path $run 'build-b\artifact-manifest.json')
        if ($a -cne $b) { throw 'Artifact reproducibility failed' }
        Write-Output 'PASS: all artifact hashes identical'
    }
    return
}
if ($Step -eq 'Image') {
    $context = Join-Path $run 'execution'
    Checked 'python' @('-B',(Join-Path $packaging 'execution_inputs.py'),'--artifacts',(Join-Path $run 'build-a'),
        '--preparation',(Join-Path $lab 'l2-runs\20260915\preparation'),'--reference',(Join-Path $lab 'reports\l2-reference-windows.json'),'--output',$context)
    Checked 'docker' @('build','--network','none','--platform','linux/amd64','-t',$imageTag,'-f',(Join-Path $context 'Dockerfile.linux-test'),$context)
    return
}
if ($Step -eq 'Inspect') {
    Checked 'docker' @('image','inspect',$imageTag,'--format','{{.Id}}')
    Checked 'docker' @('ps','-a','--filter',('name=xoc-l3-' + $RunId),'--format','{{.Names}} {{.Status}}')
    return
}
$results = Join-Path $run 'results'
if (-not (Test-Path -LiteralPath $results)) { New-Item -ItemType Directory -Path $results | Out-Null }
$phase = $Step.ToLowerInvariant()
New-Output (Join-Path $results $phase)
if ($Step.StartsWith('Original')) {
    # Immutable original L2 image. Never rebuild/relabel/overwrite it.
    $imageId = 'sha256:be002dede580ab1bb0ede1c27caf951813eadd3c2971a600eb05d7a421f77ece'
    $arguments = @($phase.Substring('original'.Length),'--output',('/results/' + $phase))
    $entrypoint = @()
} else {
    $imageId = & docker image inspect $imageTag --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Image unavailable' }
    if ($Step -in @('Profile','Benchmark')) {
        $entrypoint = @('--entrypoint','/opt/xoc-l2/bin/python')
        $arguments = @('-I','-B','-m','xoc_mcp_lab_harness.l3_benchmark',$phase,'--output',('/results/' + $phase))
    } else {
        $entrypoint = @()
        $arguments = @($phase,'--output',('/results/' + $phase))
    }
}
Checked 'docker' (@('run','--rm','--name',$name) + $limits + @('--network','none','--read-only',
    '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--mount',"type=bind,source=$results,target=/results") + $entrypoint + @($imageId) + $arguments)
