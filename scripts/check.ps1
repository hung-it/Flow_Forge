$ErrorActionPreference = 'Stop'
function Run-Check([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Check failed: $Command" }
}
$repoPath = Split-Path $PSScriptRoot
Push-Location "$repoPath/backend"
try {
    Run-Check { uv sync --frozen }
    Run-Check { uv run ruff check . ../scripts/verify_stack.py }
    Run-Check { uv run mypy }
    Run-Check { uv run pytest --cov=flowforge.contracts --cov-report=xml:../reports/coverage.xml --cov-fail-under=80 }
    Run-Check { uv export --quiet --frozen --no-emit-project --format requirements-txt --output-file ../reports/requirements-audit.txt }
    Run-Check { uv run pip-audit -r ../reports/requirements-audit.txt --no-deps --disable-pip }
} finally { Pop-Location }
Push-Location "$repoPath/frontend"
try {
    Run-Check { npm ci }
    Run-Check { npm run lint }
    Run-Check { npm run typecheck }
    Run-Check { npm run build }
    Run-Check { npm audit --audit-level=high }
} finally { Pop-Location }
Push-Location "$repoPath/tooling"
try {
    Run-Check { npm ci }
    Run-Check { npm run diagrams }
    Run-Check { npm audit --audit-level=high }
} finally { Pop-Location }
