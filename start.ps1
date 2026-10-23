$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 scripts/start.py @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python scripts/start.py @args
} else {
    throw 'Install Python 3.11+ and Node.js 22.12+, then reopen PowerShell.'
}
exit $LASTEXITCODE
