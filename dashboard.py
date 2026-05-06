from flask import Flask, render_template, jsonify, request
import subprocess
import sys
import os
import csv
import threading
import uuid
from pathlib import Path
from glob import glob

app      = Flask(__name__, template_folder="templates")
BASE_DIR = Path(__file__).parent
PYTHON   = sys.executable

BOTS = [
    {
        "id":          "admin_field_updater",
        "name":        "Admin Field Updater",
        "subtitle":    "Automatisation navigateur",
        "description": "Met à jour des valeurs sur des pages web en boucle, piloté par data.csv via Chromium.",
        "color":       "primary",
        "icon":        "bi-browser-chrome",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session",
                "description": "Ouvre un navigateur pour connexion manuelle. À lancer une seule fois — relancer si la session expire.",
                "warning":     "Nécessite une interaction manuelle dans Chromium. Préférer le terminal si la fenêtre n'apparaît pas.",
                "inputs":      [],
                "outputs":     ["session.json"],
            },
            {
                "file":        "admin_field_updater.py",
                "name":        "Bot Updater",
                "description": "Lit data.csv et met à jour chaque URL en boucle (clic Éditer → saisie valeur → clic Enregistrer).",
                "inputs":      ["data.csv (colonnes : id, url, value)", "session.json"],
                "outputs":     ["results_XXXXXX.csv"],
            },
        ],
        "results_globs": ["results_*.csv"],
    },
    {
        "id":          "solvimon_api",
        "name":        "Solvimon API",
        "subtitle":    "Subscriptions en masse",
        "description": "Duplique une subscription source et l'assigne à chaque customer via l'API REST Solvimon (sandbox ou prod).",
        "color":       "success",
        "icon":        "bi-lightning-charge-fill",
        "scripts": [
            {
                "file":        "inspect_subscription.py",
                "name":        "Inspecter la Subscription",
                "description": "Liste les subscriptions disponibles et affiche les champs de la subscription source. Étape de vérification avant le bulk.",
                "inputs":      ["API_KEY + SUB_ID configurés dans le fichier"],
                "outputs":     [],
            },
            {
                "file":        "solvimon_bulk_subscriptions.py",
                "name":        "Créer les Subscriptions",
                "description": "Pour chaque customer : POST /copy (duplication exacte) → PATCH customer_id + status ACTIVE.",
                "inputs":      ["customers.csv (colonne : customer_id)"],
                "outputs":     ["results/results_solvimon_XXXXXX.csv"],
            },
        ],
        "results_globs": ["results/results_solvimon_*.csv"],
    },
    {
        "id":          "payplug_keygen",
        "name":        "Portail PayPlug - API Key Gen",
        "subtitle":    "Gestion des clés API",
        "description": "Génère des clés API PayPlug par compte via le portail, puis vérifie leur association via OAuth2 + planners.",
        "color":       "warning",
        "icon":        "bi-key-fill",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session PayPlug",
                "description": "Ouvre le portail PayPlug pour connexion manuelle. À lancer une seule fois.",
                "warning":     "Nécessite une interaction manuelle dans Chromium. Préférer le terminal si la fenêtre n'apparaît pas.",
                "inputs":      [],
                "outputs":     ["session.json"],
            },
            {
                "file":        "keygen.py",
                "name":        "Générer les Clés API",
                "description": "Pour chaque compte : switch de compte → Générer une nouvelle clé → saisie du nom → capture credentials.",
                "inputs":      ["accounts.csv (account_id, account_name, key_name)", "session.json"],
                "outputs":     ["results/results_payplug_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "key_type",
                        "label":   "Type de clé",
                        "options": [
                            {"value": "1", "label": "OAuth2 — Client ID + Secret"},
                            {"value": "2", "label": "API Key — clé unique"},
                        ],
                        "default": "1",
                    },
                    {
                        "id":      "environment",
                        "label":   "Environnement",
                        "options": [
                            {"value": "1", "label": "Test"},
                            {"value": "2", "label": "Live"},
                        ],
                        "default": "1",
                    },
                ],
            },
            {
                "file":        "verify.py",
                "name":        "Vérifier les Clés",
                "description": "Token OAuth2 → création d'un planner test sur le companyRef → 201 = clé OK, 403 = mauvais compte.",
                "inputs":      ["results/results_payplug_*.csv (dernier fichier auto-détecté)"],
                "outputs":     ["results/verify_XXXXXX.csv"],
            },
        ],
        "results_globs": ["results/results_payplug_*.csv", "results/verify_*.csv"],
    },
]

running_jobs: dict = {}


@app.route("/")
def index():
    return render_template("index.html", bots=BOTS)


@app.route("/api/run", methods=["POST"])
def run_script():
    data     = request.json
    bot_id   = data.get("bot_id")
    script   = data.get("script")
    job_id   = str(uuid.uuid4())[:8]
    bot_path = BASE_DIR / bot_id

    stdin_input = data.get("stdin_input", "")
    running_jobs[job_id] = {"lines": [], "done": False, "rc": None}

    def worker():
        try:
            proc = subprocess.Popen(
                [PYTHON, script],
                cwd=str(bot_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if stdin_input else subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            if stdin_input:
                proc.stdin.write(stdin_input)
                proc.stdin.close()
            for line in proc.stdout:
                running_jobs[job_id]["lines"].append(line.rstrip())
            proc.wait()
            running_jobs[job_id]["rc"] = proc.returncode
        except Exception as e:
            running_jobs[job_id]["lines"].append(f"[ERREUR DASHBOARD] {e}")
        finally:
            running_jobs[job_id]["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/output/<job_id>")
def get_output(job_id):
    job = running_jobs.get(job_id, {})
    return jsonify({
        "lines": job.get("lines", []),
        "done":  job.get("done", False),
        "rc":    job.get("rc"),
    })


@app.route("/api/results/<bot_id>")
def get_results(bot_id):
    bot = next((b for b in BOTS if b["id"] == bot_id), None)
    if not bot:
        return jsonify([])

    all_files: list = []
    for pattern in bot["results_globs"]:
        all_files.extend(glob(str(BASE_DIR / bot_id / pattern)))

    files = sorted(set(all_files), reverse=True)[:5]

    result = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
            result.append({
                "name":    Path(f).name,
                "rows":    rows,
                "headers": list(rows[0].keys()) if rows else [],
            })
        except Exception:
            pass

    return jsonify(result)


if __name__ == "__main__":
    print("─────────────────────────────────────────")
    print("✓ Dashboard → http://localhost:5001")
    print("  Ctrl+C pour arrêter")
    print("─────────────────────────────────────────")
    app.run(debug=False, port=5001, threaded=True)
