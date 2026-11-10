"""
CompanyStatusBot — serveur web minimal (sans le reste du dashboard/bots).

Protégé par Basic Auth (APP_USERNAME / APP_PASSWORD, variables d'environnement —
APP_PASSWORD est obligatoire, l'app refuse de servir des réponses sans elle).

Routes :
  GET  /            formulaire
  POST /run          lance un job en arrière-plan, retourne {"job_id": ...}
  GET  /status/<id>  journal + état du job (polling JSON)
  GET  /download/<id> CSV de résultats

Limitation assumée : en Sandbox, pas de SSO Okta / MFA possible ici (pas de
navigateur visible) — connexion par identifiants email+mdp (sans MFA) ou import
direct d'un sandbox_session.json déjà authentifié. Pour un compte avec MFA
obligatoire, utiliser le script local (admin_payplug/company_status_updater.py).
"""
import asyncio
import csv
import functools
import io
import json
import os
import threading
import uuid

from flask import Flask, Response, jsonify, render_template, request

from bot import ENVIRONMENTS, STATUS_OPTIONS, run_job

app = Flask(__name__)

APP_USERNAME = os.environ.get("APP_USERNAME", "payplug")
APP_PASSWORD = os.environ.get("APP_PASSWORD")  # pas de valeur par défaut — volontaire

SANDBOX_SESSION_FILE = "sandbox_session.json"

JOBS: dict = {}


def requires_auth(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not APP_PASSWORD:
            return Response(
                "APP_PASSWORD n'est pas configuré sur le serveur — accès bloqué par sécurité.",
                503,
            )
        auth = request.authorization
        if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
            return Response(
                "Authentification requise.", 401,
                {"WWW-Authenticate": 'Basic realm="CompanyStatusBot"'},
            )
        return view(*args, **kwargs)
    return wrapped


def load_cached_sandbox_session():
    if not os.path.exists(SANDBOX_SESSION_FILE):
        return None
    try:
        with open(SANDBOX_SESSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_sandbox_session(state: dict) -> None:
    try:
        with open(SANDBOX_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


@app.route("/", methods=["GET"])
@requires_auth
def index():
    return render_template(
        "index.html",
        status_options=STATUS_OPTIONS,
        has_cached_sandbox=os.path.exists(SANDBOX_SESSION_FILE),
    )


@app.route("/run", methods=["POST"])
@requires_auth
def run():
    environment  = request.form.get("environment", "prod")
    status_value = request.form.get("status", "2")
    raw_ids      = request.form.get("ids", "")
    company_ids  = [x.strip() for x in raw_ids.replace(",", "\n").splitlines() if x.strip()]

    if not company_ids:
        return jsonify({"error": "Aucun ID fourni."}), 400
    if environment not in ENVIRONMENTS:
        return jsonify({"error": "Environnement invalide."}), 400
    if status_value not in STATUS_OPTIONS:
        return jsonify({"error": "Statut invalide."}), 400

    credentials = None
    login    = request.form.get("sandbox_login", "").strip()
    password = request.form.get("sandbox_password", "").strip()
    if login and password:
        credentials = (login, password)

    prod_storage_state = None
    prod_file = request.files.get("prod_session")
    if prod_file and prod_file.filename:
        try:
            prod_storage_state = json.load(prod_file.stream)
        except Exception:
            return jsonify({"error": "session.json Production invalide (JSON illisible)."}), 400

    sandbox_storage_state = None
    sandbox_file = request.files.get("sandbox_session")
    if sandbox_file and sandbox_file.filename:
        try:
            sandbox_storage_state = json.load(sandbox_file.stream)
        except Exception:
            return jsonify({"error": "sandbox_session.json invalide (JSON illisible)."}), 400
    elif environment == "sandbox":
        sandbox_storage_state = load_cached_sandbox_session()

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"log": [], "done": False, "results": None, "error": None}

    def log(line: str) -> None:
        JOBS[job_id]["log"].append(line)

    def worker():
        try:
            results, new_sandbox_state = asyncio.run(run_job(
                environment, status_value, company_ids, credentials,
                sandbox_storage_state, prod_storage_state, log,
            ))
            JOBS[job_id]["results"] = results
            if new_sandbox_state:
                save_sandbox_session(new_sandbox_state)
            if not results:
                JOBS[job_id]["error"] = "Aucun résultat — voir le journal (connexion probablement en échec)."
        except Exception as e:
            JOBS[job_id]["error"] = str(e)
        finally:
            JOBS[job_id]["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
@requires_auth
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable"}), 404
    return jsonify({
        "log":          job["log"],
        "done":         job["done"],
        "error":        job["error"],
        "result_count": len(job["results"]) if job["results"] else 0,
    })


@app.route("/download/<job_id>")
@requires_auth
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job["results"]:
        return Response("Aucun résultat disponible.", 404)

    buf    = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "status_cible", "result", "message"])
    writer.writeheader()
    writer.writerows(job["results"])

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=company_status_{job_id}.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
