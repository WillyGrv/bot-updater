from flask import Flask, render_template, jsonify, request
import subprocess
import sys
import os
import re
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
        "description": "Mise à jour de champs web en boucle via Chromium.",
        "color":       "primary",
        "icon":        "bi-browser-chrome",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session",
                "description": "Sauvegarde les cookies de session.",
                "warning":     "Interaction manuelle requise dans Chromium.",
                "inputs":      [],
                "outputs":     ["session.json"],
            },
            {
                "file":        "admin_field_updater.py",
                "name":        "Bot Updater",
                "description": "Lit data.csv et applique la valeur saisie sur chaque URL.",
                "inputs":      ["data.csv (colonnes : id, url)", "session.json"],
                "outputs":     ["results_XXXXXX.csv"],
                "params": [
                    {
                        "id":     "fields",
                        "type":   "field_selector",
                        "label":  "Champs à modifier",
                        "fields": [
                            {
                                "id":          "master_company_id",
                                "label":       "Compte Master",
                                "field_type":  "text",
                                "value_mode":  "fixed",
                                "placeholder": "ex: 12345",
                            },
                            {
                                "id":         "account_manager",
                                "label":      "Account Manager",
                                "field_type": "select",
                                "value_mode": "fixed",
                                "options": [
                                    {"value": "",      "label": "Aucun"},
                                    {"value": "-1",    "label": "Pas d'account manager"},
                                    {"value": "110",   "label": "Audrey Alia"},
                                    {"value": "160",   "label": "Audrey Galy"},
                                    {"value": "18",    "label": "Antoine Grimaud"},
                                    {"value": "10388", "label": "Anaïs Nesse"},
                                    {"value": "10594", "label": "antoine Rousseau"},
                                    {"value": "10323", "label": "Antoine Raynaud"},
                                    {"value": "20651", "label": "Arthur Robert_payplug"},
                                    {"value": "161",   "label": "Alessandro Ursini"},
                                    {"value": "10255", "label": "Chiara Chaignaud"},
                                    {"value": "10280", "label": "Christelle Mentor"},
                                    {"value": "20692", "label": "Clément Willk-Fabia"},
                                    {"value": "30",    "label": "Eric Cohen"},
                                    {"value": "10553", "label": "Erwan Dronne"},
                                    {"value": "35",    "label": "François Bureau"},
                                    {"value": "10589", "label": "Fannie Lauze"},
                                    {"value": "100",   "label": "Federica Narbone"},
                                    {"value": "10550", "label": "Gaetan Coatleven"},
                                    {"value": "20721", "label": "Gautier Toulemonde"},
                                    {"value": "10199", "label": "Irène de Giorgio"},
                                    {"value": "10324", "label": "Ilyes Djebnoune"},
                                    {"value": "10225", "label": "Juliette Manyères"},
                                    {"value": "10595", "label": "Jonathan Mayamona"},
                                    {"value": "10226", "label": "Kenza Guerinat"},
                                    {"value": "124",   "label": "Ludovica Durelli"},
                                    {"value": "10244", "label": "Marie Bruguera"},
                                    {"value": "127",   "label": "Martina Foggiano"},
                                    {"value": "20631", "label": "Matthew Houtart"},
                                    {"value": "10327", "label": "Michelangelo Palumbo"},
                                    {"value": "67",    "label": "Marie-Rebecca El Hachem"},
                                    {"value": "10486", "label": "Nathan Duc"},
                                    {"value": "10556", "label": "Oceane Schenker"},
                                    {"value": "10590", "label": "Pedro Rodrigues"},
                                    {"value": "10434", "label": "Romane Jarillon"},
                                    {"value": "20661", "label": "Romain Pastureau"},
                                    {"value": "10309", "label": "Ronan Ponce"},
                                    {"value": "10592", "label": "Ulysse Hottier"},
                                    {"value": "125",   "label": "Xavier Lespine"},
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
        "results_globs":   ["results_*.csv"],
        "editable_csvs":   [{"file": "data.csv", "label": "data.csv — urls à traiter"}],
        "test_mode_script": "admin_field_updater.py",
    },
    {
        "id":          "solvimon_api",
        "name":        "Solvimon API",
        "subtitle":    "Subscriptions en masse",
        "description": "Duplication de subscriptions en masse via l'API Solvimon.",
        "color":       "success",
        "icon":        "bi-lightning-charge-fill",
        "scripts": [
            {
                "file":        "inspect_subscription.py",
                "name":        "Inspecter la Subscription",
                "description": "Vérifie la connexion API et inspecte la subscription source.",
                "inputs":      ["API_KEY + SUB_ID configurés dans le fichier"],
                "outputs":     [],
            },
            {
                "file":        "solvimon_bulk_subscriptions.py",
                "name":        "Créer les Subscriptions",
                "description": "POST /copy → PATCH customer_id + ACTIVE pour chaque ligne.",
                "inputs":      ["customers.csv (colonne : customer_id)"],
                "outputs":     ["results/results_solvimon_XXXXXX.csv"],
            },
        ],
        "results_globs":    ["results/results_solvimon_*.csv"],
        "editable_csvs":    [{"file": "customers.csv", "label": "customers.csv — customer IDs"}],
        "test_mode_script": "solvimon_bulk_subscriptions.py",
    },
    {
        "id":          "cockpit_identifier",
        "name":        "Cockpit Identifier",
        "subtitle":    "Password recovery UDV",
        "description": "Saisie automatique d'email de récupération sur chaque UDV via le Cockpit interne.",
        "color":       "danger",
        "icon":        "bi-shield-lock-fill",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session Cockpit",
                "description": "Sauvegarde les cookies de session Cockpit.",
                "warning":     "Interaction manuelle requise dans Chromium.",
                "inputs":      [],
                "outputs":     ["session.json"],
            },
            {
                "file":        "cockpit_identifier.py",
                "name":        "Lancer le bot",
                "description": "Pour chaque ID UDV du CSV, soumet l'email de récupération.",
                "inputs":      ["data.csv (colonne : id_udv)", "session.json"],
                "outputs":     ["results/results_cockpit_XXXXXX.csv"],
                "params": [
                    {
                        "id":          "email",
                        "type":        "text",
                        "label":       "Email de récupération",
                        "placeholder": "ex: user@example.com",
                    },
                ],
            },
        ],
        "results_globs":    ["results/results_cockpit_*.csv"],
        "editable_csvs":    [{"file": "data.csv", "label": "data.csv — IDs UDV"}],
        "test_mode_script": "cockpit_identifier.py",
    },
    {
        "id":          "payplug_keygen",
        "name":        "Portail PayPlug - API Key Gen",
        "subtitle":    "Gestion des clés API",
        "description": "Génération et vérification de clés API PayPlug par compte.",
        "color":       "warning",
        "icon":        "bi-key-fill",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session PayPlug",
                "description": "Sauvegarde les cookies de session PayPlug.",
                "warning":     "Interaction manuelle requise dans Chromium.",
                "inputs":      [],
                "outputs":     ["session.json"],
            },
            {
                "file":        "keygen.py",
                "name":        "Générer les Clés API",
                "description": "Switch compte → génère clé OAuth2 → JWT → company_ref.",
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
        ],
        "results_globs":    ["results/results_payplug_*.csv"],
        "editable_csvs":    [{"file": "accounts.csv", "label": "accounts.csv — comptes PayPlug"}],
        "test_mode_script": "keygen.py",
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


@app.route("/api/testmode/<bot_id>")
def get_testmode(bot_id):
    bot = next((b for b in BOTS if b["id"] == bot_id), None)
    if not bot or not bot.get("test_mode_script"):
        return jsonify({"test_mode": None})
    path    = BASE_DIR / bot_id / bot["test_mode_script"]
    content = path.read_text(encoding="utf-8")
    match   = re.search(r'TEST_MODE\s*=\s*(True|False)', content)
    return jsonify({"test_mode": match.group(1) == "True" if match else None})


@app.route("/api/testmode/<bot_id>", methods=["POST"])
def set_testmode(bot_id):
    bot = next((b for b in BOTS if b["id"] == bot_id), None)
    if not bot or not bot.get("test_mode_script"):
        return jsonify({"ok": False})
    value   = request.json.get("test_mode", True)
    path    = BASE_DIR / bot_id / bot["test_mode_script"]
    content = path.read_text(encoding="utf-8")
    new     = re.sub(r'TEST_MODE\s*=\s*(True|False)', f'TEST_MODE = {str(value)}', content)
    path.write_text(new, encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/csv/<bot_id>/<filename>")
def read_csv(bot_id, filename):
    path = BASE_DIR / bot_id / filename
    if not path.exists():
        return jsonify({"content": ""}), 200
    return jsonify({"content": path.read_text(encoding="utf-8")})


@app.route("/api/csv/<bot_id>/<filename>", methods=["POST"])
def write_csv(bot_id, filename):
    content = request.json.get("content", "")
    path    = BASE_DIR / bot_id / filename
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True})


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
