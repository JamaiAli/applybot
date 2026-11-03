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
| `/search`  | Cherche + score, puis te présente les meilleures offres (≥ 8/10) |
| `/offres`  | Réaffiche les offres à valider, **sans** relancer la recherche |
| `/status`  | État : total, scorées, candidatées, à valider, distribution |
| `/pause`   | Met en pause la recherche quotidienne automatique |
| `/resume`  | La réactive |

Chaque offre arrive en **message** (titre, entreprise, score, raison, lieu,
lien), avec le **CV + la lettre en PDF** s'ils sont générés, et **3 boutons** :

- **✅ Valider** — enregistre ton feu vert explicite, l'offre passe « prête à
  postuler ». *Le bot ne soumet jamais sans ce ✅.*
- **✏️ Modifier** — tu réponds avec ce qu'il faut changer dans le CV/la lettre.
- **❌ Ignorer** — écarte l'offre.

## Règles de sécurité câblées

- **Toi seul** peux utiliser le bot : chaque commande et chaque clic vérifient
  ton `TELEGRAM_USER_ID`. Tout autre utilisateur reçoit « ⛔ Accès réservé. »
- **Dry-run par défaut** : rien n'est soumis sans ton ✅.
- **Aucun secret en clair** : token et ID vivent dans `.env` (ignoré par git).

## Ce qui reste (Phase 4b)

Le ✅ marque l'offre « prête ». La **soumission automatique** (remplissage du
formulaire + téléversement du CV sur le site employeur) est la brique 3 : un
moteur Playwright qui, lui, peut téléverser le fichier. À faire ensuite.
