# bot-updater — Documentation Projet

## Vue d'ensemble

Suite d'automatisation opérationnelle tournant sur une interface web Flask (dashboard) accessible sur `http://localhost:5001`. Elle regroupe 4 bots, chacun dédié à une tâche métier précise, tous pilotés depuis un tableau de bord unifié.

**Stack technique :**
- Python 3 + Flask (dashboard + routing)
- Playwright (automation navigateur Chromium, async)
- pandas + csv (manipulation de données)
- python-dotenv (gestion des credentials)
- Bootstrap 5 + CSS custom dark-theme

---

## Architecture du Dashboard

```
bot-updater/
├── dashboard.py                  # Serveur Flask, liste BOTS, routing /api/
├── templates/index.html          # UI : grille de sélection + vue détail
├── static/dashboard.css          # Dark theme CSS
├── .env                          # Credentials réels (gitignored)
├── .env.example                  # Template credentials (sans valeurs réelles)
├── requirements.txt
│
├── admin_field_updater/
│   ├── admin_field_updater.py    # Bot 1 — Playwright admin
│   ├── login.py
│   └── data.csv
│
├── payplug_keygen/
│   ├── payplug_keygen.py         # Bot 2 — Playwright keygen
│   └── login.py
│
├── solvimon_api/
│   ├── solvimon_bulk_subscriptions.py  # Bot 3 — API REST bulk
│   └── inspect_subscription.py        # Outil de vérification standalone
│
└── cockpit_identifier/
    ├── cockpit_identifier.py     # Bot 4 — Playwright UDV
    ├── login.py
    └── data.csv
```

### Flux dashboard → bot

1. L'utilisateur sélectionne un bot sur la grille d'accueil (`#view-list`)
2. Il clique pour ouvrir la vue détail (`#view-detail`) qui affiche scripts + panneaux CSV
3. Il remplit les paramètres dans la modale et clique "Lancer"
4. Le dashboard démarre le script Python via `subprocess.Popen`, pipe stdin avec les params, capture stdout en temps réel via `/api/output/<job_id>` (polling SSE-like)
5. Un résultat CSV est généré dans `results/`

### Paramètres — types supportés

| Type | Comportement dashboard | Lecture dans le script |
|------|----------------------|----------------------|
| `text` | Input texte libre | `input().strip()` |
| `field_selector` | Sélecteur de champs JSON | `json.loads(input())` |
| radio options | Boutons radio | `input().strip().lower()` |

Les lignes stdin sont jointes par `\n` et envoyées d'un coup.

---

## Bot 1 — Admin Field Updater

**Couleur :** Bleu (`primary`)  
**Fichier :** `admin_field_updater/admin_field_updater.py`

**Objectif :** Mettre à jour en masse un champ dans l'interface admin PayPlug pour une liste de clients issus d'un CSV.

**Paramètres :** sélecteur de champ + valeur maître unique appliquée à toutes les lignes  
**Session :** `session.json` (Playwright `storage_state`) — connexion préalable via `login.py`  
**Output :** CSV `results/results_admin_*.csv`

---

## Bot 2 — PayPlug Keygen

**Couleur :** Vert (`success`)  
**Fichier :** `payplug_keygen/payplug_keygen.py`

**Objectif :** Génération automatique de clés API PayPlug via l'interface admin.

**Session :** `session.json` — connexion préalable via `login.py`  
**JWT :** Vérification `company_ref` dans le token JWT  
**Output :** CSV des clés générées

---

## Bot 3 — Solvimon Bulk Subscriptions

**Couleur :** Orange (`warning`)  
**Fichiers :**
- `solvimon_api/solvimon_bulk_subscriptions.py` — duplication en masse
- `solvimon_api/inspect_subscription.py` — vérification standalone

**Objectif :** Dupliquer une subscription Solvimon source vers N customers issus d'un `customers.csv`.

**Flux en 2 étapes (depuis dashboard) :**
1. **Script 1 — Vérifier la Subscription** : appelle `GET /pricing-plan-subscriptions/{id}`, affiche les détails, confirme l'existence
2. **Script 2 — Créer les Subscriptions** : vérifie d'abord, puis boucle sur le CSV → `POST /copy` + `PATCH customer_id + ACTIVE`

**Paramètres :**
- `env` : `prod` ou `sandbox` (radio)
- `sub_id` : ID de la subscription source (text)

**Environnements :**
```python
ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",   # ⚠ URL à confirmer — NXDOMAIN actuellement
    "sandbox": "https://test.api.solvimon.com/v1",  # OK — résout vers 34.102.242.154
}
```

**⚠ Point bloquant actuel :** `payplug.solvimon.com` ne résout pas (NXDOMAIN). La bonne URL de prod Solvimon n'a pas encore été confirmée. À mettre à jour dans les deux scripts dès confirmation.

**Credentials :** `SOLVIMON_API_KEY` dans `.env`  
**Output :** CSV `results/results_solvimon_*.csv` : `customer_id, status, subscription_id, message`

---

## Bot 4 — Cockpit Identifier

**Couleur :** Rouge (`danger`)  
**Fichier :** `cockpit_identifier/cockpit_identifier.py`

**Objectif :** Pour une liste d'IDs UDV (CSV), naviguer sur chaque page de récupération de mot de passe de l'intranet Cockpit, remplir un email unique (saisi une fois au lancement), cliquer Submit.

**URL cible :** `https://internal-payment.gcp.dlns.io/intranet/admin/udvs/password?idUdv={idUdv}`  
**Session :** `session.json` — connexion via `login.py` sur `https://internal-payment.gcp.dlns.io/cockpit/public/#/login`

**Paramètre :** `email` (text) — saisi une fois, appliqué à tous les UDVs  
**Output :** CSV `results/results_cockpit_*.csv` : `id_udv, email, status, message`

**data.csv format :**
```
id_udv
7614
3348
...
```

---

## Setup & Prérequis

```bash
# Créer et activer le venv
python3 -m venv venv
source venv/bin/activate

# Installer les dépendances
pip install -r requirements.txt
playwright install chromium

# Configurer les credentials
cp .env.example .env
# Éditer .env et remplir SOLVIMON_API_KEY

# Lancer le dashboard
python dashboard.py
# → http://localhost:5001
```

**Note VSCode :** Configurer l'interpréteur sur `./venv/bin/python` pour résoudre les imports (Cmd+Shift+P → Python: Select Interpreter).

---

## Points d'attention / Limitations connues

| Sujet | Détail |
|-------|--------|
| Port 5001 déjà utilisé | `lsof -ti:5001 \| xargs kill -9` avant relancement |
| Sessions Playwright | Doivent être régénérées manuellement via `login.py` si expirées |
| Solvimon prod URL | `payplug.solvimon.com` → NXDOMAIN — à corriger |
| TEST_MODE | Flag `TEST_MODE = False` dans les scripts — passer à `True` pour tester sur 1 ligne |
| Gitignore | `.env` et `session.json` sont gitignorés — ne pas commiter les credentials |

---

## Besoins Futurs Potentiels

### Court terme
- **Corriger l'URL Solvimon prod** — obtenir la bonne base URL auprès de l'équipe Solvimon/PayPlug
- **Persistance des sessions** — mécanisme de détection d'expiration + re-login automatique
- **Validation des CSV** — vérifier les colonnes requises avant de lancer le bot

### Moyen terme
- **Retry automatique** — relancer les lignes en erreur sans relancer tout le batch
- **Pagination des résultats** — afficher les CSV de résultats dans le dashboard avec tri/filtre
- **Logs persistants** — historique des runs dans une petite DB SQLite plutôt que des CSV éparpillés
- **Notifications** — alerte Slack/email à la fin d'un batch

### Long terme
- **Authentification dashboard** — protéger l'accès si déployé sur un serveur partagé
- **Scheduling** — lancer des bots à heure fixe via cron intégré au dashboard
- **Multi-instance** — permettre plusieurs jobs en parallèle (actuellement 1 job actif à la fois)
- **Nouveaux bots** — la structure est extensible : ajouter une entrée dans `BOTS` dans `dashboard.py`

---

## Credentials & Sécurité

| Variable | Où | Usage |
|----------|-----|-------|
| `SOLVIMON_API_KEY` | `.env` | Auth API Solvimon (header `X-API-KEY`) |
| `SOLVIMON_SUB_ID` | `.env` (optionnel) | ID subscription par défaut (non utilisé actuellement) |
| Sessions Playwright | `*/session.json` | Cookies/localStorage pour les bots navigateur |

**Règle :** Ne jamais commiter `.env` ni `session.json`. Le `.gitignore` couvre ces fichiers.
