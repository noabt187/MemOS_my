$ErrorActionPreference = "Stop"

$launcher = Join-Path $PSScriptRoot "scripts\start_memos_topic.ps1"
& $launcher @args
exit $LASTEXITCODE
