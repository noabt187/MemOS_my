$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "memos_chat.py"
$projectRoot = Split-Path -Parent $PSScriptRoot
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uv) {
    throw "uv was not found. Install uv first, then run this script again."
}

Push-Location $projectRoot
try {
    & $uv.Source run --frozen --extra skill-mem python $scriptPath @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
