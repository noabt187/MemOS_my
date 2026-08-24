param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker\docker-compose.yml"
$environmentFile = Join-Path $projectRoot ".env"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop, then run this script again."
}

if (-not (Test-Path -LiteralPath $environmentFile -PathType Leaf)) {
    throw "Missing $environmentFile. Copy docker\.env.example to .env and fill in the model configuration first."
}

$arguments = @("compose", "-f", $composeFile, "up", "-d", "--wait")
if ($Build) {
    $arguments += "--build"
}

& docker @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& docker compose -f $composeFile ps
exit $LASTEXITCODE
