# Exercises only a freshly generated disposable Compose project.
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path $PSScriptRoot
Push-Location $repoPath
try {
    uv run --frozen --project backend python -X utf8 scripts/verify_stack.py
    if ($LASTEXITCODE -ne 0) { throw 'Isolated Compose verification failed' }
} finally {
    Pop-Location
}
