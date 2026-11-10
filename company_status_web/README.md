# Bots PayPlug — version web

Version autonome, hébergeable gratuitement, de deux bots du dashboard. Un sélecteur en
haut du formulaire permet de choisir lequel lancer :

- **CompanyStatusBot** (`admin_payplug/company_status_updater.py`) — change le Company
  status d'un ou plusieurs comptes (Production ou Sandbox).
- **ScrapBot** (`admin_payplug/scrapbot.py`) — extrait un ou plusieurs champs (SIRET,
  email, adresse, etc.) d'une liste de fiches entreprise (Production uniquement, comme
  l'original — pas de notion de Sandbox pour ce bot).

Les deux partagent le même serveur, la même authentification et le même mécanisme de
journal en direct / téléchargement CSV.

## ⚠️ Limitation importante — Sandbox

Ce service tourne **headless** (pas de navigateur visible). Il ne peut donc **pas**
gérer le SSO Okta ni une validation MFA. En Sandbox, deux options seulement :

1. Identifiants email + mot de passe d'un compte **sans MFA obligatoire**.
2. Importer un fichier `sandbox_session.json` déjà authentifié, exporté depuis une
   session locale valide (celui généré par le script local après un login Okta manuel).

Si le compte Sandbox impose systématiquement la MFA, ce flux web ne peut pas aboutir —
utiliser le script local (`admin_payplug/company_status_updater.py`) dans ce cas.

La Production, elle, n'a pas cette contrainte : il suffit de fournir un `session.json`
valide (même mécanisme que le dashboard local).

## ⚠️ Sécurité

Cet outil peut changer le statut (Enregistré / Bloqué / Désactivé) de vrais comptes
marchands. Avant de le partager :

- **Protège l'accès** : l'app est verrouillée par Basic Auth (`APP_USERNAME` /
  `APP_PASSWORD`, variables d'environnement). Choisis un mot de passe fort et ne le
  partage qu'avec les personnes de confiance.
- Le `session.json` Production n'est **jamais** écrit sur disque côté serveur — il n'est
  utilisé qu'en mémoire, le temps du run, puis jeté. Un hébergeur gratuit n'est pas un
  endroit sûr pour stocker durablement un identifiant aussi puissant.
- Le `sandbox_session.json`, lui, est mis en cache sur le disque du serveur (même
  logique que le script local) pour éviter de se reconnecter à chaque run — acceptable
  car c'est un environnement de test, mais garde ça en tête.
- Pense à faire tourner ce service sur **HTTPS** (Render/Railway le font par défaut).

## Déploiement gratuit sur Render

1. Pousse ce dossier (`company_status_web/`) sur un dépôt GitHub (le reste du repo
   `bot-updater` peut rester privé/inchangé — ce dossier est autonome).
2. Sur [render.com](https://render.com), crée un compte gratuit puis **New → Web Service**.
3. Connecte le dépôt GitHub contenant ce dossier.
4. Configuration :
   - **Root Directory** : `company_status_web` (si ce dossier est dans un repo plus
     large comme `bot-updater`, sinon laisser vide).
   - **Runtime** : Docker (Render détecte le `Dockerfile` automatiquement).
   - **Plan** : Free.
5. Dans l'onglet **Environment**, ajoute les variables :
   - `APP_USERNAME` (ex: `payplug`)
   - `APP_PASSWORD` (choisis un mot de passe fort — **obligatoire**, l'app refuse de
     répondre sans elle)
6. Déploie. Render fournit une URL `https://<nom-du-service>.onrender.com`.
7. Partage l'URL + les identifiants Basic Auth avec la personne qui doit l'utiliser.

Note : le plan Free de Render met le service en veille après une période d'inactivité
— le premier chargement après une pause peut prendre ~30-60 secondes (le temps que le
conteneur redémarre). C'est normal.

## Utilisation

1. Ouvre l'URL, entre le login/mot de passe (Basic Auth).
2. Choisis le **bot** en haut du formulaire (CompanyStatusBot ou ScrapBot).

**CompanyStatusBot :**
3. Choisis l'environnement (Production / Sandbox) et le statut cible.
4. Colle les IDs de comptes (un par ligne, ou séparés par des virgules).
5. Fournis la session nécessaire :
   - Production : upload du `session.json`.
   - Sandbox : identifiants email+mdp, ou upload d'un `sandbox_session.json`.
6. Clique sur Lancer, suis le journal en direct, télécharge le CSV de résultats à la fin.

**ScrapBot :**
3. Colle les IDs de comptes à scraper (un par ligne, ou séparés par des virgules).
4. Coche les données à extraire (Company Ref, SIRET, email, adresse, etc.).
5. Upload le `session.json` Production (seul environnement supporté par ce bot).
6. Clique sur Lancer, suis le journal en direct, télécharge le CSV de résultats à la fin.

## Lancer en local (test avant déploiement)

```bash
cd company_status_web
pip install -r requirements.txt
playwright install chromium
export APP_USERNAME=payplug
export APP_PASSWORD=change-moi
python app.py
# → http://localhost:10000
```
