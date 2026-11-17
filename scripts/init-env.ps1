# Create a private local configuration without overwriting an existing .env.
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path $PSScriptRoot
$target = Join-Path $repoPath '.env'
if (Test-Path -LiteralPath $target) { throw '.env already exists; keep its signing key and edit it manually.' }
$bytes = New-Object byte[] 48
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
$secret = [Convert]::ToBase64String($bytes)
$template = Get-Content -Raw -LiteralPath (Join-Path $repoPath '.env.example')
$text = $template.Replace('FLOWFORGE_JWT_SECRET=', "FLOWFORGE_JWT_SECRET=$secret")
$file = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
$writer = New-Object System.IO.StreamWriter($file, (New-Object System.Text.UTF8Encoding($false)))
try { $writer.Write($text) } finally { $writer.Dispose() }
Write-Host 'Created .env with a private signing key. Existing keys are never replaced.'
