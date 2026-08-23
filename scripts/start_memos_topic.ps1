$ErrorActionPreference = "Stop"

$serveMode = $args.Count -gt 0 -and $args[0] -eq "serve"
if ($serveMode) {
    $scriptPath = Join-Path $PSScriptRoot "memos_frontend_api.py"
    $forwardArgs = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }
}
else {
    $scriptPath = Join-Path $PSScriptRoot "memos_topic.py"
    $forwardArgs = $args
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uv) {
    throw "uv was not found. Install uv first, then run this script again."
}

Push-Location $projectRoot
try {
    if ($serveMode) {
        & $uv.Source run --link-mode copy --frozen --extra skill-mem python $scriptPath @forwardArgs
    }
    else {
        & $uv.Source run --link-mode copy --frozen python $scriptPath @forwardArgs
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
