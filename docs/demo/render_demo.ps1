param(
    [ValidateSet("powershell", "bash")]
    [string]$Variant = "powershell"
)

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$tapeName = "search-api-demo.$Variant.tape"
$tapePath = Join-Path $PSScriptRoot $tapeName

if (-not (Test-Path $tapePath)) {
    Write-Error "Tape not found: $tapePath"
    exit 1
}

if (-not (Get-Command vhs -ErrorAction SilentlyContinue)) {
    Write-Error "vhs is not installed or not on PATH."
    Write-Host "Install with: go install github.com/charmbracelet/vhs@latest"
    exit 1
}

Push-Location $repoRoot
try {
    & vhs $tapePath
}
finally {
    Pop-Location
}
