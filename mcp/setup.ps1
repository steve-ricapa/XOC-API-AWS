param([string]$PythonExecutable = 'python')
$ErrorActionPreference = 'Stop'
$labRoot = $PSScriptRoot
$version = & $PythonExecutable -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0 -or $version -ne '3.13.1') { throw 'This lock was tested with CPython 3.13.1. Select that interpreter explicitly.' }
$venv = Join-Path $labRoot '.venv'
$labPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venv)) {
    & $PythonExecutable -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the dedicated lab venv.' }
}
if (-not (Test-Path -LiteralPath $labPython -PathType Leaf)) { throw 'Existing .venv is incomplete; do not overwrite it automatically.' }
$lock = Join-Path $labRoot 'requirements.lock.txt'
if (-not (Test-Path -LiteralPath $lock -PathType Leaf)) { throw 'Missing laboratory dependency lock.' }
& $labPython -m pip --isolated install --require-virtualenv --disable-pip-version-check --index-url https://pypi.org/simple --only-binary=:all: --no-deps -r $lock
if ($LASTEXITCODE -ne 0) { throw 'Lab dependency installation failed.' }
& $labPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Lab dependency consistency check failed.' }
Write-Output 'XOC-MCP-LAB ready. No production environment was modified.'
