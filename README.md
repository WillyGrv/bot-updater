# Bot Updater

Suite de 3 bots d'automatisation + dashboard web local.

## Bots disponibles

| Dossier | Nom | Description |
|---|---|---|
| `admin_field_updater/` | Admin Field Updater | Mise à jour de champs sur des pages web via Playwright |
| `solvimon_api/` | Solvimon API | Création en masse de subscriptions via l'API REST Solvimon |
| `payplug_keygen/` | Portail PayPlug - API Key Gen | Génération de clés API PayPlug + vérification via OAuth2/planners |

---

## Installation

### 1. Prérequis
- Python 3.11+
- pip

### 2. Environnement virtuel

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright pandas requests flask
playwright install chromium
```

### 3. Variables d'environnement (Solvimon)

```bash
cp .env.example .env
# Éditer .env avec tes clés
```

---

## Lancer le dashboard

```bash
source .venv/bin/activate
python dashboard.py
# → http://localhost:5001
```

Le dashboard permet de lancer chaque script, voir la sortie en temps réel et consulter les résultats CSV.

---

## Bots

### Admin Field Updater (`admin_field_updater/`)

Met à jour des valeurs sur des pages web en boucle via Chromium.

```bash
cd admin_field_updater
python login.py              # 1 seule fois — sauvegarde la session
python admin_field_updater.py
```

**Fichiers :**
- `data.csv` — colonnes : `id`, `url`, `value`
- `session.json` — généré automatiquement (ignoré par git)

---

### Solvimon API (`solvimon_api/`)

Duplique une subscription source et l'assigne à chaque customer.

```bash
cd solvimon_api
python inspect_subscription.py       # vérifier la connexion API
python solvimon_bulk_subscriptions.py
```

**Configuration :** via `.env` à la racine (`SOLVIMON_API_KEY`, `SOLVIMON_SUB_ID`)

**Fichiers :**
- `customers.csv` — colonne : `customer_id`
- `results/` — logs générés automatiquement (ignorés par git)

---

### Portail PayPlug - API Key Gen (`payplug_keygen/`)

Génère des clés API PayPlug par compte et vérifie leur association.

```bash
cd payplug_keygen
python login.py       # 1 seule fois — sauvegarde la session PayPlug
python keygen.py      # génère les clés (demande type + environnement au démarrage)
python verify.py      # vérifie les clés via OAuth2 + planners
```

**Fichiers :**
- `accounts.csv` — colonnes : `account_id`, `account_name`, `key_name`
- `session.json` — généré automatiquement (ignoré par git)
- `results/` — logs générés automatiquement (ignorés par git)

---

## Structure du projet

```
bot-updater/
├── dashboard.py              ← dashboard web (Flask)
├── templates/index.html
├── static/dashboard.css
├── .env.example              ← template de configuration
├── admin_field_updater/
│   ├── login.py
│   ├── admin_field_updater.py
│   └── data.csv
├── solvimon_api/
│   ├── inspect_subscription.py
│   ├── solvimon_bulk_subscriptions.py
│   └── customers.csv
└── payplug_keygen/
    ├── login.py
    ├── keygen.py
    ├── verify.py
    └── accounts.csv
```
