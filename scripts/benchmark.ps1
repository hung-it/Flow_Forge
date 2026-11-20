param([switch]$Quick)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot)
try {
    $options = @()
    if ($Quick) { $options += "--quick" }
    uv run --frozen --project backend python -X utf8 scripts/benchmark.py @options
    if ($LASTEXITCODE -ne 0) { throw "Isolated benchmark failed (exit $LASTEXITCODE)" }
}
finally {
    Pop-Location
}
