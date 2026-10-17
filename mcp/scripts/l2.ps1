param(
    [Parameter(Mandatory=$true)][ValidateSet('Prepare','Export','Build','Rebuild','Image','Tests','Smoke','Provenance','Benchmark','Inspect')][string]$Step,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9-]{2,40}$')][string]$RunId,
    [string]$Preparation = 'l2-runs\20260915\preparation'
)
$ErrorActionPreference = 'Stop'
$lab = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$target = Get-Content -Raw -LiteralPath (Join-Path $lab 'packaging\linux-target.json') | ConvertFrom-Json
$run = Join-Path $lab ('l2-runs\' + $RunId)
$packaging = Join-Path $lab 'packaging'
$imageTag = 'xoc-mcp-l2-local:' + $RunId
$container = 'xoc-l2-' + $RunId + '-' + $Step.ToLowerInvariant()
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
function Assert-New([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "Refusing to overwrite existing output: $Path" }
}
$limits = @('--platform','linux/amd64','--memory','512m','--cpus','2','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges')
if ($Step -eq 'Prepare') {
    if (-not (Test-Path -LiteralPath $run)) { New-Item -ItemType Directory -Path $run | Out-Null }
    Assert-New (Join-Path $run 'preparation')
    Invoke-Checked 'docker' @('pull','--platform','linux/amd64',$target.image)
    Invoke-Checked 'docker' (@('run','--rm','--name',$container) + $limits + @('--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=128m',
        '--mount',"type=bind,source=$packaging,target=/inputs,readonly",'--mount',"type=bind,source=$run,target=/output",
        '--entrypoint','/var/lang/bin/python',$target.image,'-I','-B','/inputs/prepare_linux.py','--output','/output/preparation'))
    return
}
if ($Step -eq 'Export') {
    $backend = (Resolve-Path -LiteralPath (Join-Path $lab '..\XOC-API-AWS')).Path
    Invoke-Checked 'python' @('-B',(Join-Path $packaging 'build_artifacts.py'),'export','--backend',$backend,'--output',(Join-Path $run 'sources-a'))
    return
}
if ($Step -in @('Build','Rebuild')) {
    $suffix = if ($Step -eq 'Build') { 'a' } else { 'b' }
    $source = Join-Path $run ('sources-' + $suffix)
    if ($Step -eq 'Rebuild') {
        Assert-New $source
        # New independent staging; same approved revision and current lab sources.
        $backend = (Resolve-Path -LiteralPath (Join-Path $lab '..\XOC-API-AWS')).Path
        Invoke-Checked 'python' @('-B',(Join-Path $packaging 'build_artifacts.py'),'export','--backend',$backend,'--output',$source)
    }
    Assert-New (Join-Path $run ('build-' + $suffix))
    Invoke-Checked 'docker' (@('run','--rm','--name',$container) + $limits + @('--network','none','--read-only',
        '--mount',"type=bind,source=$packaging,target=/inputs,readonly",'--mount',"type=bind,source=$source,target=/sources,readonly",
        '--mount',"type=bind,source=$run,target=/output",'--entrypoint','/var/lang/bin/python',$target.image,
        '-I','-B','/inputs/build_artifacts.py','build','--staging','/sources','--output',('/output/build-' + $suffix),
        '--runtime-lock','/inputs/requirements-linux-runtime.lock.txt','--test-lock','/inputs/requirements-linux-test.lock.txt'))
    if ($Step -eq 'Rebuild') {
        $a = Get-Content -Raw -LiteralPath (Join-Path $run 'build-a\artifact-manifest.json')
        $b = Get-Content -Raw -LiteralPath (Join-Path $run 'build-b\artifact-manifest.json')
        if ($a -cne $b) { throw 'Reproducibility failed: artifact manifests differ' }
        Write-Output 'PASS: identical artifact manifests and SHA256 after two clean builds'
    }
    return
}
if ($Step -eq 'Image') {
    $context = Join-Path $run 'execution'
    $prep = (Resolve-Path -LiteralPath (Join-Path $lab $Preparation)).Path
    if (-not $prep.StartsWith($lab + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Preparation must stay inside the laboratory' }
    Invoke-Checked 'python' @('-B',(Join-Path $packaging 'execution_inputs.py'),'--artifacts',(Join-Path $run 'build-a'),
        '--preparation',$prep,'--reference',(Join-Path $lab 'reports\l2-reference-windows.json'),'--output',$context)
    Invoke-Checked 'docker' @('build','--network','none','--platform','linux/amd64','-t',$imageTag,
        '-f',(Join-Path $context 'Dockerfile.linux-test'),$context)
    return
}
if ($Step -eq 'Inspect') {
    Invoke-Checked 'docker' @('image','inspect',$imageTag,'--format','{{.Id}}')
    Invoke-Checked 'docker' @('ps','-a','--filter',('name=xoc-l2-' + $RunId),'--format','{{.Names}} {{.Status}}')
    return
}
$results = Join-Path $run 'results'
if (-not (Test-Path -LiteralPath $results)) { New-Item -ItemType Directory -Path $results | Out-Null }
Assert-New (Join-Path $results $Step.ToLowerInvariant())
# Resolve local tag to immutable image ID before running. Only controlled output mount.
$imageId = & docker image inspect $imageTag --format '{{.Id}}'
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Image is not built or invalid' }
Invoke-Checked 'docker' (@('run','--rm','--name',$container) + $limits + @('--network','none','--read-only',
    '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--mount',"type=bind,source=$results,target=/results",$imageId,
    $Step.ToLowerInvariant(),'--output',('/results/' + $Step.ToLowerInvariant())))
