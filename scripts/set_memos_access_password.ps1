$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    throw "uv was not found. Install uv first, then run this script again."
}

Push-Location $projectRoot
try {
    & $uv.Source run --link-mode copy --frozen python "$PSScriptRoot\memos_app_auth.py" @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
