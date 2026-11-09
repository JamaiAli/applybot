<#
.SYNOPSIS
    Synchronise les fichiers secrets et la base de données d'ApplyPilot vers ton VPS.
.DESCRIPTION
    Transfère ~/.applypilot/.env, experiences.json, applypilot.db et les tokens Gmail.
.EXAMPLE
    .\deploy\sync_to_vps.ps1 -VpsHost "ubuntu@123.45.67.89"
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$VpsHost
)

$localDir = "$HOME\.applypilot"
$remoteDir = "~/.applypilot"

Write-Host "🚀 Préparation du transfert vers $VpsHost..." -ForegroundColor Cyan

# Créer le dossier distant si nécessaire
ssh $VpsHost "mkdir -p $remoteDir"

$files = @(".env", "experiences.json", "applypilot.db", "token.json", "credentials.json")

foreach ($file in $files) {
    $filePath = Join-Path $localDir $file
    if (Test-Path $filePath) {
        Write-Host "📤 Envoi de $file..." -ForegroundColor Yellow
        scp "$filePath" "${VpsHost}:${remoteDir}/${file}"
    } else {
        Write-Host "ℹ️ $file ignoré (non présent localement)" -ForegroundColor DarkGray
    }
}

Write-Host "✅ Données synchronisées avec succès sur le VPS !" -ForegroundColor Green
Write-Host "💡 Pour redémarrer le bot sur le VPS : ssh $VpsHost 'sudo systemctl restart applypilot'" -ForegroundColor Cyan
