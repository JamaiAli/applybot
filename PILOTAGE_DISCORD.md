# Pilotage Discord — mise en route

Le bot est écrit et testé. Il ne manque que **2 valeurs à toi** à coller dans
`~/.applypilot/.env`, puis une commande pour le lancer.

## 1. Créer le bot (5 min, une seule fois)

1. Va sur https://discord.com/developers/applications → **New Application**
   (nom libre, ex. « ApplyPilot »).
2. Onglet **Bot** → **Reset Token** → **Copy**. C'est le `DISCORD_BOT_TOKEN`.
3. Onglet **OAuth2** → **URL Generator** :
   - Scopes : coche **bot** + **applications.commands**
   - Bot Permissions : **Send Messages**, **Embed Links**, **Attach Files**
   - Copie l'URL générée en bas, ouvre-la, et **ajoute le bot à un serveur à toi**
     (crée-en un perso si besoin — le bot t'enverra aussi des DM).

## 2. Récupérer ton user ID

Discord → **Paramètres** → **Avancés** → active **Mode développeur**.
Puis clic droit sur **ton profil** → **Copier l'identifiant**. C'est le
`DISCORD_USER_ID` (une suite de chiffres).

## 3. Coller les 2 valeurs

Dans `~/.applypilot/.env` (déjà préparé) :

```
DISCORD_BOT_TOKEN=colle_ici_le_token
DISCORD_USER_ID=colle_ici_ton_id
```

## 4. Lancer

```bash
applypilot bot
```

Le bot se connecte et enregistre ses commandes. Depuis ton téléphone (ou le
serveur), tu as alors :

| Commande   | Effet |
|------------|-------|
| `/search`  | Cherche + score, puis te poste les meilleures offres (≥ 8/10) |
| `/offres`  | Réaffiche les offres à valider, **sans** relancer la recherche |
| `/status`  | Entonnoir : total, scorées, candidatées, à valider, distribution |
| `/pause`   | Met en pause la recherche quotidienne automatique |
| `/resume`  | La réactive |

Chaque offre arrive en **carte** (titre, entreprise, score, raison, lieu), avec
le **CV + la lettre en PDF joints** s'ils sont déjà générés, et **3 boutons** :

- **✅ Valider** — enregistre ton feu vert explicite et passe l'offre en
  « prête à postuler ». *La soumission réelle reste une étape à part — le bot
  ne soumet jamais sans ce ✅.*
- **✏️ Modifier** — ouvre un champ pour dire quoi changer dans le CV/la lettre.
- **❌ Ignorer** — écarte l'offre.

## Règles de sécurité câblées

- **Toi seul** peux utiliser le bot : chaque commande et chaque clic vérifient
  ton `DISCORD_USER_ID`. Un autre utilisateur reçoit « ⛔ Accès réservé. »
- **Dry-run par défaut** : rien n'est soumis sans ton ✅.
- **Aucun secret en clair** : token et ID vivent dans `.env` (ignoré par git).

## Ce qui reste (Phase 4b)

Le ✅ marque l'offre « prête ». La **soumission automatique** (remplissage du
formulaire + téléversement du CV sur le site de l'employeur) est la brique 3 :
un moteur Playwright qui, lui, peut téléverser le fichier. À faire ensuite.
