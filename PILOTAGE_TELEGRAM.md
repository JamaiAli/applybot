# Pilotage Telegram — mise en route

Le bot est écrit et testé. Il ne manque que **2 valeurs à toi** à coller dans
`~/.applypilot/.env`, puis une commande pour le lancer. (On a choisi Telegram
plutôt que Discord : pas de restriction de compte pour les bots perso.)

## 1. Créer le bot (2 min, une seule fois)

1. Sur Telegram, cherche **@BotFather** (le vrai, coché bleu) et ouvre-le.
2. Envoie **/newbot**.
3. Donne un **nom** (ce que tu veux, ex. « ApplyPilot »).
4. Donne un **@username** qui finit par **bot** (ex. `ali_applypilot_bot`).
5. BotFather te répond avec un **token** du type
   `123456789:AAE...xxxxx`. C'est le `TELEGRAM_BOT_TOKEN`.
   → colle-le dans `.env` (ou donne-le-moi, mais c'est un secret — mieux vaut
   le coller toi-même).

## 2. Récupérer ton user ID

1. Sur Telegram, cherche **@userinfobot** et ouvre-le.
2. Envoie **/start** (ou n'importe quel message).
3. Il te répond avec ton **Id** (une suite de chiffres). C'est le
   `TELEGRAM_USER_ID` — pas un secret, tu peux me le donner.

## 3. Coller les 2 valeurs

Dans `~/.applypilot/.env` :

```
TELEGRAM_BOT_TOKEN=colle_ici_le_token
TELEGRAM_USER_ID=colle_ici_ton_id
```

## 4. Lancer

```bash
applypilot telegram-bot
```

Puis, sur Telegram, **ouvre une conversation avec ton bot** (clique son
@username) et envoie **/start**. Tu as alors :

| Commande   | Effet |
|------------|-------|
| `/scan [métier] [ville]` | ⚡ **Scan live** : parcourt HelloWork & Indeed en temps réel, **ignore tout ce qui est déjà en base**, et diffuse immédiatement les toutes dernières offres inédites |
| `/search [métier] [ville]` | Cherche sur Indeed + score, puis te présente les meilleures offres |
| `/offres [métier] [ville]` | Affiche les offres fraîches (≤ 7j) prêtes en base, **sans** scraper |
| `/job`      | Jobs étudiants + intérim Indeed (défaut : Bourges, ou `/job Orléans`) |
| `/trackers` | Suivi des consultations de CV en temps réel (Tracer Links) |
| `/done`     | Marque la dernière candidature préparée comme « postulée » |
| `/status`   | État : total, scorées, candidatées, à valider, distribution |
| `/pause`    | Met en pause la recherche quotidienne automatique |
| `/resume`   | La réactive |

Chaque offre arrive en **message** (titre, entreprise, score, raison, lieu,
lien), avec le **CV + la lettre en PDF** s'ils sont générés, et **3 boutons** :

- **✅ Valider** — enregistre ton feu vert explicite, l'offre passe « prête à
  postuler ». *Le bot ne soumet jamais sans ce ✅.*
- **✏️ Modifier** — tu réponds avec ce qu'il faut changer dans le CV/la lettre.
- **❌ Ignorer** — écarte l'offre.

## Sources d'offres : HelloWork + Indeed

Le bot scrape **deux sources** en veille automatique : **HelloWork** à chaque
cycle (toutes les ~10 min, `BOT_CYCLE_MINUTES`) et **Indeed** moins souvent
(toutes les ~2 h par défaut, `BOT_INDEED_CYCLE_HOURS` dans `~/.applypilot/.env`)
car il est plus lourd en requêtes. Quand de nouvelles offres Indeed sont
trouvées, elles sont **envoyées automatiquement** sur Telegram (anti-doublon).
Une erreur sur une source (blocage, timeout) **ne casse pas** l'autre : on log
et on continue. Les offres Indeed
sont pré-filtrées « stage + bien cyber », taguées par domaine, puis **scorées
par le LLM comme HelloWork** (seules les ≥ 8/10 te sont présentées). Le message
indique la **source** (🔎), le **domaine** cyber principal (🏷) et l'**ancienneté**
de l'offre (📅 ex. « il y a 2 j »). **Les offres Indeed sont prioritaires** : elles
sont classées avant celles de HelloWork ; à l'intérieur de chaque source, tri de
la plus récente à la plus ancienne (sans date en dernier). Les doublons entre
sources (même titre + entreprise + ville) sont retirés automatiquement — c'est
l'offre Indeed qui est gardée —, et une offre déjà envoyée n'est jamais renvoyée.

### Brancher Indeed (une seule fois)

Indeed réutilise le scraper local `indeed-scraper` (hors du repo). Indique son
dossier — **au choix**, l'un des deux (aucun chemin en dur dans le code) :

- dans `~/.applypilot/.env` :
  ```
  INDEED_SCRAPER_PATH=C:\Users\jamai\indeed-scraper
  ```
- **ou** dans `~/.applypilot/searches.yaml`, bloc `indeed.scraper_path`.

### Activer / désactiver, régler les requêtes

Tout se règle dans `~/.applypilot/searches.yaml` (modèle :
`src/applypilot/config/searches.example.yaml`), **sans toucher au code** :

```yaml
# Âge max des offres présentées, TOUTES SOURCES (filtre sur la date de
# publication). 0 = pas de filtre. Les offres sans date connue sont gardées.
max_age_days: 30

indeed:
  enabled: true            # false → Indeed ignoré, HelloWork continue seul
  scraper_path: ""         # vide → variable d'env INDEED_SCRAPER_PATH
  location: "France"       # ex. "Paris", "Île-de-France"
  pages: 1                 # pages par requête (20 offres/page)
  include_alternance: false
  # max_age_days: 30       # (optionnel) surcharge le max_age_days global pour Indeed
  queries:                 # dict {domaine: [requêtes]} OU liste plate
    "Généraliste cyber": ["stage cybersécurité", ...]
    # … ajoute/retire des domaines et des requêtes librement
```

### Tester Indeed sans rien envoyer (dry-run)

```powershell
$env:INDEED_SCRAPER_PATH = "C:\Users\jamai\indeed-scraper"
.\.venv\Scripts\python.exe scripts\indeed_dryrun.py --pages 1
```

Affiche les offres normalisées (titre, entreprise, lieu, source, domaine, lien)
**sans** écrire en base ni envoyer sur Telegram.

### `/job` — jobs étudiants (HelloWork + Indeed)

La commande **`/job`** (défaut : Bourges ; `/job Orléans` pour une autre ville)
cherche des **jobs étudiants / week-end** en **live** sur HelloWork **et** Indeed,
fusionne les deux (sans doublons) et affiche chaque offre avec sa date (📅) et un
repère « 🗓️ week-end » le cas échéant. Recherche live : pas de base, pas de
scoring. Stages/alternances et temps-plein « secs » sont écartés. Réglages dans
`~/.applypilot/searches.yaml`, bloc `indeed_student` (activer/désactiver, ville,
`weekend_only`, `cher_only`, tri `date`/`pertinence`, requêtes par catégorie).
Une panne d'Indeed n'empêche pas les résultats HelloWork.

## Suivi des consultations de CV (Tracer Links)

Inspiré de JobNavigator, ApplyPilot intègre un **système d'accusé de réception invisible** pour savoir quand un recruteur consulte ton CV :

1. **Génération transparente** : Quand tu prépares une candidature (via le bouton Telegram ou en ligne de commande), le CV LaTeX généré intègre un lien unique sur le mot **« Portfolio »** dans l'en-tête.
2. **Détection en temps réel** : Dès que le recruteur clique sur le lien depuis ton PDF :
   - Son adresse IP, son horodatage et son appareil (iPhone, Windows Chrome, Mac Safari, etc.) sont consignés.
   - Le serveur traceur renvoie immédiatement une redirection HTTP 302 invisible vers ton portfolio réel (`https://jamaiali.github.io`).
   - Tu reçois **instantanément une alerte Telegram** :
     `🔔 Consultation de ton CV détectée !`
     `Entreprise : Thales`
     `Offre : Ingénieur DevOps Réseaux`
     `Appareil : iPhone (Safari)`
3. **Consulter l'historique** :
   Tape **/trackers** dans Telegram pour voir le nombre total de lectures et le détail par offre.
4. **Exposition publique (optionnel)** :
   Par défaut, le serveur écoute sur `http://localhost:8765`. Pour que les recruteurs externes puissent résoudre le lien, tu peux exposer ce port avec **Cloudflare Tunnel** (gratuit) :
   ```bash
   cloudflared tunnel --url http://localhost:8765
   ```
   Puis ajouter dans `~/.applypilot/.env` :
   ```env
   TRACER_PORT=8765
   TRACER_BASE_URL=https://ton-tunnel.trycloudflare.com
   ```

## Règles de sécurité câblées

- **Toi seul** peux utiliser le bot : chaque commande et chaque clic vérifient
  ton `TELEGRAM_USER_ID`. Tout autre utilisateur reçoit « ⛔ Accès réservé. »
- **Dry-run par défaut** : rien n'est soumis sans ton ✅.
- **Aucun secret en clair** : token et ID vivent dans `.env` (ignoré par git).

## Ce qui reste (Phase 4b)

Le ✅ marque l'offre « prête ». La **soumission automatique** (remplissage du
formulaire + téléversement du CV sur le site employeur) est la brique 3 : un
moteur Playwright qui, lui, peut téléverser le fichier. À faire ensuite.
