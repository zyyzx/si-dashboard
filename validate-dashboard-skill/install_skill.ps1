# install_skill.ps1
# Double-click this file (or right-click > Run with PowerShell) to install
# the validate-dashboard skill into Claude Cowork.

$src = Join-Path $PSScriptRoot "."
$skillsBase = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\skills-plugin"

# Find the skills directory (it's nested under two GUIDs)
$skillsDirs = Get-ChildItem -Path $skillsBase -Recurse -Filter "skills" -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName "docx") }

if (-not $skillsDirs) {
    Write-Host "ERROR: Could not find Claude skills directory under $skillsBase" -ForegroundColor Red
    Write-Host "Make sure Claude Cowork has been run at least once."
    Read-Host "Press Enter to exit"
    exit 1
}

$skillsDir = $skillsDirs[0].FullName
$dst = Join-Path $skillsDir "validate-dashboard"

Write-Host "Installing validate-dashboard skill..."
Write-Host "  Source : $src"
Write-Host "  Dest   : $dst"

Copy-Item -Path $src -Destination $dst -Recurse -Force
Remove-Item -Path (Join-Path $dst "install_skill.ps1") -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done! Restart Claude Cowork to pick up the new skill." -ForegroundColor Green
Read-Host "Press Enter to exit"
