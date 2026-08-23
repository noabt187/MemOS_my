$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "scripts\memos_chat.py"
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uv) {
    throw "uv was not found. Install uv first, then run this script again."
}

Push-Location $PSScriptRoot
try {
    & $uv.Source run --link-mode copy --frozen --extra skill-mem python $scriptPath @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
