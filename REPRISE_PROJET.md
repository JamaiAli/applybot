# ApplyPilot FR — État du projet (reprise)

> À donner à une nouvelle session Claude Code pour reprendre le projet.
> Dernière mise à jour : 2026-11-04.

## 1. En deux phrases

Agent de recherche de **stage** (et de jobs étudiants/intérim) pour **Ali Jamai**
(élève-ingénieur cybersécurité, INSA Centre Val de Loire, Bac+4, diplôme 2028 ;
cherche un **stage de 6 mois à partir du 05/04/2027** en intégration IT &
cybersécurité, autour d'Active Directory / Entra ID / GRC). Piloté **depuis
Telegram**, il **trouve** les offres (HelloWork), les **score** (Groq, gratuit),
génère un **CV LaTeX adapté par offre**, et **pré-remplit les formulaires de
candidature** dans un navigateur sur le PC.

## 2. Emplacements

- **Code** : `C:\Users\jamai\OneDrive\Desktop\applypilot-fr` — branche `phase2-adaptation-fr`.
  venv Python 3.12 (`.venv`), install éditable (`pip install -e ".[dev]"`).
- **Données utilisateur** : `C:\Users\jamai\.applypilot\` :
  - `applypilot.db` (SQLite), `profile.json`, `resume.txt`, `experiences.json`
    (11 projets réels, source anti-invention), `qa_knowledge.json` (réponses aux
    questions de formulaire), `cv_job_etudiant.pdf` (CV fixe jobs étudiants).
  - `cv_latex/` (templates + CV générés + `assets/photo.jpg`), `browser_profile/`
    (profil Chrome persistant pour l'auto-apply — connexions d'Ali).
  - `.env` (secrets, hors git) : `GROQ_API_KEY`, `GEMINI_API_KEY` (secours),
    `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID=8673210207`, `ADZUNA_*` (abandonné),
    `FRANCE_TRAVAIL_*` (inutilisable), `DISCORD_*` (abandonné).

## 3. Lancer le bot

Dans PowerShell (le PC doit rester allumé) :
```
cd C:\Users\jamai\OneDrive\Desktop\applypilot-fr
.venv\Scripts\python -m applypilot.telegram_bot
```
(ou double-clic `lancer_bot.bat`). **Quand redémarrer** : après toute modif de
`telegram_bot.py`, `bot_core.py`, `cv_latex.py`, `scorer.py`, `hellowork.py`.
**Pas besoin** pour `autoapply.py` (lancé en sous-processus à chaque candidature).

## 4. Commandes Telegram (bot @ali_applypilot_bot, accès restreint à son ID)

- `/offres` — meilleures offres de stage (HelloWork uniquement, triées **par date**,
  ≥ 8/10) avec date de publication. Boutons : **📨 Préparer ma candidature**,
  ✅ J'ai postulé, ✏️ Modifier le CV, ❌ Ignorer. Une décision **retire la carte** du fil.
- `/search` — force une recherche+scoring+post immédiats.
- `/done` — marque la dernière offre « Préparée » comme **postulée** (disparaît).
- `/job [ville]` — **jobs étudiants + missions intérim** (défaut Bourges), chacun
  avec bouton **🚀 Postuler** (utilise `cv_job_etudiant.pdf`).
- `/status`, `/pause`, `/resume` (veille auto), `/clean` (efface la discussion).
- **Veille automatique toutes les 10 min** : scrape HelloWork (tri date) → score
  → envoie en DM **seulement les nouvelles** offres ≥ 8/10 (anti-spam via
  `notified_offers.json`).

## 5. Comment ça marche (flux « Préparer »)

1. Clic **📨 Préparer** → génère le **CV LaTeX adapté** (Groq choisit les
   expériences RÉELLES pertinentes + rédige l'**accroche** alignée sur l'angle de
   l'offre ; jamais d'invention), l'envoie sur Telegram.
2. Ouvre un **navigateur VISIBLE sur le PC** (profil persistant) avec un bouton
   flottant **« 📝 Compléter le formulaire »**.
3. Ali navigue / se **connecte lui-même** si besoin, puis **clique le bouton** →
   le bot remplit : champs texte (nom/email/tél…), **déroulants courants**
   (Pays→France, Autorisé→Oui, Comment connu→LinkedIn… déterministe, sans LLM),
   déroulants/lettre via Groq, **boutons Oui/Non**, et joint le **CV**.
4. Ali vérifie, saisit son **mot de passe** s'il y en a (le bot n'y touche
   jamais), clique **Postuler**, puis `/done`.
   - **Depuis le téléphone** : pas d'auto-remplissage (le navigateur est sur le
     PC) → Ali reçoit **CV + lien direct** pour postuler à la main.

## 6. Fichiers de code clés

- `src/applypilot/discovery/hellowork.py` — scraper HelloWork (httpx + BeautifulSoup,
  sélecteurs `a[data-cy=offerTitle]`, descriptions complètes via **JSON-LD**,
  **tri `st=date`**), + `search_live()` pour `/job`.
- `src/applypilot/scoring/scorer.py` — scoring LLM (prompt FR, éligibilité France,
  pause `SCORE_DELAY_SECONDS=4` entre appels, priorité à l'accroche de CV).
- `src/applypilot/cv_latex.py` — génération CV LaTeX (accroche LLM adaptée +
  humanisée, faits fixes épinglés = INSA, 1 page adaptatif, photo ronde,
  compilation **pdflatex**, 3 relances avant repli).
- `src/applypilot/autoapply.py` — navigateur visible, bouton « Compléter »,
  remplissage (texte + `_fill_selects` déterministe + `_smart_fill` Groq + radios
  + upload CV), détection connexion/CAPTCHA **visible uniquement**, multi-onglets.
- `src/applypilot/telegram_bot.py` — le bot (commandes, boutons, veille 10 min).
- `src/applypilot/bot_core.py` — logique commune (offres, décisions, mappings).
- `qa_knowledge.json` — réponses d'Ali : permis **Oui**, pas de véhicule, mobile
  **toute la France**, dispo stage **05/04/2027** (6 mois), jobs étudiants
  week-ends + vacances, télétravail/prétentions indifférent, source **LinkedIn**,
  **Bac+4** diplôme **2028**, langues FR/EN.

## 7. Décisions & limites (important)

- **Sources** : **HelloWork uniquement** (gratuit, fiable, descriptions JSON-LD).
  **Adzuna supprimé** (143 offres effacées — descriptions tronquées).
  **France Travail** inutilisable (API réservée aux entreprises).
- **LinkedIn : REFUSÉ** (risque de bannissement du compte perso + CGU + contournement
  anti-bot). **Indeed via omkarcloud/Botasaurus : REFUSÉ** (évasion anti-bot).
  **jobspy Indeed** : configuré dans `searches.yaml` (France), à TESTER
  (`run discover --source indeed`) — peut être bloqué par Cloudflare (sans dommage).
- **Discord abandonné** (compte flaggé « manipulation de plateforme ») → remplacé
  par **Telegram**.
- **Palier gratuit LLM** (Groq/min, Gemini/jour) : le scoring est **limité en
  vitesse** → les nouvelles offres sont scorées **progressivement**, pas d'un coup.
- **Auto-remplissage = PC uniquement** (le navigateur tourne où tourne le bot).
- **Pas d'hébergement 24/7 encore** : piste **Oracle Cloud Free Tier** discutée,
  non faite (laisserait tout tourner sans le PC, sauf l'auto-remplissage).

## 8. Règles absolues (ne jamais enfreindre)

- **Anti-invention** : le CV ne contient que des expériences/compétences/chiffres
  RÉELS (de `experiences.json`) ; seule l'accroche est rédigée, à partir de faits.
- **Aucune soumission sans le ✅ explicite d'Ali** (dry-run par défaut).
- **Le bot ne crée JAMAIS de compte et ne saisit JAMAIS de mot de passe** — Ali
  le fait lui-même, une fois, dans le profil navigateur persistant.
- **Aucun contournement de détection de bot ni de CAPTCHA** (→ on s'arrête et on
  passe la main). Secrets uniquement dans `.env` (git-ignoré).

## 9. Pistes suivantes (au choix)

1. **Greenhouse / Lever** : adaptateurs via leurs **API publiques gratuites**
   (entreprises FR tech) — beaucoup plus d'offres, 100 % légitime. *Recommandé.*
2. **Déploiement Oracle Cloud Free Tier** : bot 24/7 sans le PC.
3. **Élargir les mots-clés HelloWork** (GRC, cloud, réseau, DevSecOps, admin sys).
4. Fiabiliser l'**upload CV sur SAP SuccessFactors** (widget « + » maison).
5. Brancher la **génération de lettre de motivation** LaTeX dans le bouton.
