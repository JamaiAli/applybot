<#
.SYNOPSIS
    Déploie le traceur de liens de CV d'ApplyPilot sur Cloudflare Workers (offre gratuite).
.DESCRIPTION
    À lancer UNE fois, depuis la racine du projet :

        .\deploy\deploy_tracer_worker.ps1

    Le script :
      1. te connecte à Cloudflare (une page s'ouvre dans ton navigateur) ;
      2. crée l'espace de stockage des clics (KV « TRACKERS ») ;
      3. déploie le Worker (Cloudflare peut te demander ton sous-domaine workers.dev) ;
      4. lui transmet ses trois secrets : la clé partagée avec le bot (générée ici)
         et, lus dans ~/.applypilot/.env, le jeton Telegram et ton identifiant Telegram,
         pour que les alertes partent même PC éteint ;
      5. écrit TRACER_BASE_URL et TRACER_API_KEY dans ~/.applypilot/.env ;
      6. vérifie que le Worker répond.

    Tes secrets ne sont jamais affichés ni écrits dans le dépôt.
    Relance ensuite le bot : les prochains CV porteront des liens suivis.
.PARAMETER SansTelegram
    Ne transmet pas le jeton Telegram au Worker : les clics sont comptés et visibles
    avec /trackers, mais tu ne reçois pas d'alerte en direct.
#>
param([switch]$SansTelegram)

$ErrorActionPreference = "Stop"
$workerDir = Join-Path $PSScriptRoot "tracer-worker"
$envFile = Join-Path $HOME ".applypilot\.env"

function Step($n, $text) { Write-Host "`n[$n/6] $text" -ForegroundColor Cyan }

function Get-EnvValue($name) {
    if (-not (Test-Path $envFile)) { return $null }
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -Last 1
    if (-not $line) { return $null }
    return ($line -replace "^\s*$name\s*=\s*", "").Trim().Trim('"').Trim("'")
}

function Set-EnvValue($name, $value) {
    $lines = @()
    if (Test-Path $envFile) { $lines = @(Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -notmatch "^\s*$name\s*=" }) }
    $lines += "$name=$value"
    # UTF-8 sans BOM : un BOM en tête de .env ferait ignorer la première variable.
    [System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
}

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    throw "Node.js est requis (commande npx introuvable). Installe-le depuis https://nodejs.org puis relance."
}
if (-not (Test-Path $envFile)) { throw "Fichier $envFile introuvable." }

Push-Location $workerDir
try {
    Step 1 "Connexion à Cloudflare (crée un compte gratuit sur https://dash.cloudflare.com/sign-up si besoin)"
    # « whoami » renvoie 0 même sans connexion : on lit ce qu'il affiche.
    $who = (npx --yes wrangler whoami 2>&1 | ForEach-Object { "$_" }) -join " "
    if ($who -match 'logged in' -and $who -notmatch 'not (logged|authenticated)') {
        Write-Host "    Déjà connecté."
    } else {
        Write-Host "    Une page Cloudflare s'ouvre dans ton navigateur par défaut." -ForegroundColor Yellow
        Write-Host "    Connecte-toi si besoin, puis clique sur « Allow » : tu as environ 2 minutes." -ForegroundColor Yellow
        npx --yes wrangler login
        if ($LASTEXITCODE -ne 0) { throw "Connexion à Cloudflare annulée ou expirée. Relance le script et clique sur « Allow » dans la page qui s'ouvre." }
    }

    Step 2 "Stockage des clics (espace KV « TRACKERS »)"
    if ((Get-Content "wrangler.toml" -Raw) -match 'binding\s*=\s*"TRACKERS"') {
        Write-Host "    Déjà configuré dans wrangler.toml."
    } else {
        npx --yes wrangler kv namespace create TRACKERS --binding TRACKERS --update-config
        if ($LASTEXITCODE -ne 0) { throw "Création de l'espace KV échouée." }
        if ((Get-Content "wrangler.toml" -Raw) -notmatch 'binding\s*=\s*"TRACKERS"') {
            throw "L'espace KV a été créé mais wrangler.toml n'a pas été mis à jour. Ajoute le bloc [[kv_namespaces]] affiché ci-dessus à deploy\tracer-worker\wrangler.toml, puis relance ce script."
        }
    }

    Step 3 "Premier déploiement du Worker"
    Write-Host "    Compte neuf : Cloudflare peut te demander de choisir ton sous-domaine workers.dev." -ForegroundColor Yellow
    Write-Host "    Réponds à ses questions ici (le nom choisi apparaîtra dans l'adresse des liens)." -ForegroundColor Yellow
    # Lancé sans capturer la sortie : les questions de wrangler doivent rester visibles.
    npx --yes wrangler deploy
    if ($LASTEXITCODE -ne 0) { throw "Déploiement échoué." }

    Step 4 "Secrets du Worker"
    $apiKey = Get-EnvValue "TRACER_API_KEY"
    if (-not $apiKey) {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $apiKey = -join ($bytes | ForEach-Object { $_.ToString("x2") })
        Write-Host "    Clé partagée générée."
    }
    $secrets = @{ API_KEY = $apiKey }
    if (-not $SansTelegram) {
        $botToken = Get-EnvValue "TELEGRAM_BOT_TOKEN"
        $userId = Get-EnvValue "TELEGRAM_USER_ID"
        if ($botToken -and $userId) {
            $secrets.TELEGRAM_BOT_TOKEN = $botToken
            $secrets.TELEGRAM_USER_ID = $userId
        } else {
            Write-Host "    TELEGRAM_BOT_TOKEN ou TELEGRAM_USER_ID absent de .env : pas d'alerte en direct." -ForegroundColor Yellow
        }
    }
    # Fichier temporaire hors du dépôt, supprimé aussitôt.
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("applypilot-secrets-" + [guid]::NewGuid().ToString("N") + ".json")
    try {
        [System.IO.File]::WriteAllText($tmp, ($secrets | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
        npx --yes wrangler secret bulk $tmp
        if ($LASTEXITCODE -ne 0) { throw "Envoi des secrets échoué." }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    Write-Host "    $($secrets.Count) secret(s) transmis."

    # Second passage, sans question cette fois : sert à relever l'adresse du Worker.
    $out = npx --yes wrangler deploy 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { $out | ForEach-Object { Write-Host "    $_" }; throw "Déploiement échoué." }
    $url = ($out | Select-String -Pattern 'https://[A-Za-z0-9.-]+\.workers\.dev' -AllMatches |
            ForEach-Object { $_.Matches.Value } | Select-Object -First 1)
    if (-not $url) {
        $url = (Read-Host "Adresse du Worker non repérée. Colle-la ici (https://….workers.dev)").Trim()
    }
    $url = $url.TrimEnd("/")
    if ($url -notmatch '^https://') { throw "Adresse invalide : $url" }

    Step 5 "Réglages du bot dans $envFile"
    Set-EnvValue "TRACER_BASE_URL" $url
    Set-EnvValue "TRACER_API_KEY" $apiKey
    Write-Host "    TRACER_BASE_URL=$url"
    Write-Host "    TRACER_API_KEY=(enregistrée)"

    Step 6 "Vérification"
    # Windows PowerShell 5.1 négocie par défaut une vieille version de TLS, que Cloudflare
    # refuse (« Impossible de créer un canal sécurisé SSL/TLS ») : on impose TLS 1.2.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Start-Sleep -Seconds 3
    $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 20
    if ($health.status -ne "ok") { throw "Le Worker ne répond pas correctement à $url/health." }
    $api = Invoke-RestMethod -Uri "$url/api/hits" -Headers @{ Authorization = "Bearer $apiKey" } -TimeoutSec 20
    if (-not $api.ok) { throw "Le Worker refuse la clé du bot." }

    Write-Host "`nTraceur en ligne : $url" -ForegroundColor Green
    Write-Host "Essai : ouvre $url/t/portfolio depuis ton téléphone en 4G, tu dois arriver sur ton portfolio"
    Write-Host "        et recevoir une alerte Telegram."
    Write-Host "Relance maintenant le bot pour que les prochains CV portent des liens suivis."
} finally {
    Pop-Location
}
