# CR-081 Issue D - supported dev launcher (PowerShell).
#
# Always uses --reload so source edits under src/rcm/ trigger an automatic
# worker restart. Forwards extra args to uvicorn:
#   .\scripts\run_dev.ps1 --log-level debug

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$env:PYTHONPATH = 'src'

$baseArgs = @(
    '-m', 'uvicorn', 'rcm.main:app',
    '--reload',
    '--host', '127.0.0.1',
    '--port', '8000',
    '--log-level', 'warning'
)
$extra = $args
& python @baseArgs @extra
