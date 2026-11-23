$ErrorActionPreference = 'Stop'
$gitBash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path $gitBash)) {
    throw 'Install Git for Windows (including Git Bash), or run deploy/lab.sh from a Linux Docker host.'
}
Push-Location $PSScriptRoot
try {
    & $gitBash ./lab.sh @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
