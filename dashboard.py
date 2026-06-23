from flask import Flask, render_template, jsonify, request
import subprocess
import sys
import os
import re
import csv
import json
import time
import threading
import uuid
import requests as req_lib
import urllib3
from pathlib import Path
from glob import glob

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app      = Flask(__name__, template_folder="templates")
BASE_DIR = Path(__file__).parent
PYTHON   = sys.executable

BOTS = [
    {
        "id":          "admin_payplug",
        "name":        "Admin PayPlug",
        "subtitle":    "Automatisation navigateur",
        "description": "Mise à jour de champs web en boucle via Chromium.",
        "color":       "primary",
        "icon":        "bi-browser-chrome",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session",
                "description": "Sauvegarde les cookies de session.",
                "warning":     "Connexion manuelle dans Chromium, sauf si des identifiants sont fournis ci-dessous.",
                "inputs":      [],
                "outputs":     ["session.json"],
                "params": [
                    {
                        "id":    "credentials",
                        "type":  "credentials",
                        "label": "Identifiants de connexion (optionnel)",
                    },
                ],
            },
            {
                "file":        "scrapbot.py",
                "name":        "ScrapBot",
                "description": "Lit data.csv et extrait la donnée choisie sur chaque URL.",
                "inputs":      ["data.csv (colonnes : id, url)", "session.json"],
                "outputs":     ["<prefix>_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "target",
                        "type":    "radio",
                        "label":   "Donnée à scraper",
                        "options": [
                            {"value": "company_ref",    "label": "Company Ref"},
                            {"value": "raison_sociale", "label": "Raison Sociale"},
                            {"value": "siret",          "label": "SIRET"},
                            {"value": "nom_commercial", "label": "Nom Commercial"},
                        ],
                    },
                ],
            },
            {
                "file":        "feature_flag_updater.py",
                "name":        "Ajouter un Feature Flag",
                "description": "Crée le feature flag sur chaque compte — depuis un fichier company_refs ou l'Input - Company_ref.",
                "inputs":      ["company_refs_XXXXXX.csv ou channel_accounts.csv", "session.json"],
                "outputs":     ["results_feature_flags_XXXXXX.csv"],
                "params": [
                    {
                        "id":          "flag_name",
                        "type":        "text",
                        "label":       "Nom du feature flag",
                        "placeholder": "ex: new-checkout-v2",
                    },
                    {
                        "id":      "input_source",
                        "type":    "radio",
                        "label":   "Source des company_refs",
                        "default": "csv_file",
                        "options": [
                            {"value": "csv_file", "label": "Fichier company_refs (résultats ScrapBot)"},
                            {"value": "input",    "label": "Input - Company_ref (channel_accounts.csv)"},
                        ],
                    },
                    {
                        "id":      "company_refs_file",
                        "type":    "dynamic_select",
                        "label":   "Fichier company_refs à utiliser",
                        "api":     "/api/company_refs_files/admin_payplug",
                        "show_if": {"param": "input_source", "value": "csv_file"},
                    },
                ],
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
                                "id":          "commentaire",
                                "label":       "Commentaire",
                                "field_type":  "comment",
                                "value_mode":  "fixed",
                                "placeholder": "ex: Compte validé le 21/05/2026",
                            },
                            {
                                "id":         "siret",
                                "label":      "SIRET",
                                "field_type": "text",
                                "value_mode": "dynamic",
                                "csv_column": "siret",
                            },
                            {
                                "id":          "contact_email",
                                "label":       "Email de contact",
                                "field_type":  "text",
                                "value_mode":  "fixed",
                                "placeholder": "ex: contact@example.com",
                            },
                            {
                                "id":          "master_company_id",
                                "label":       "Compte Master",
                                "field_type":  "text",
                                "value_mode":  "fixed",
                                "placeholder": "ex: 12345",
                            },
                            {
                                "id":         "type",
                                "label":      "Type",
                                "field_type": "select",
                                "value_mode": "fixed",
                                "options": [
                                    {"value": "-50", "label": "Test (en prod)"},
                                    {"value": "0",   "label": "Default"},
                                    {"value": "50",  "label": "Marchand GM"},
                                ],
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
            {
                "file":        "feature_adder.py",
                "name":        "Ajout de fonctionnalité",
                "description": "Coche les fonctionnalités sélectionnées pour chaque company_ref du CSV, puis valide.",
                "inputs":      ["channel_accounts.csv (colonnes : id, company_ref)", "session.json"],
                "outputs":     ["results/results_features_XXXXXX.csv"],
                "params": [
                    {
                        "id":    "features",
                        "type":  "checkbox_group",
                        "label": "Fonctionnalités à activer",
                        "options": [
                            {"value": "usePayplugJs",                             "label": "Use payplug js"},
                            {"value": "canChangeOffer",                           "label": "Can change offer"},
                            {"value": "cannotChoosePremiumOffer",                 "label": "Cannot choose premium offer"},
                            {"value": "requireTermsValidationForPaymentRequests", "label": "Require terms validation for payment requests"},
                            {"value": "canUseIntegratedPayments",                 "label": "Can use integrated payments"},
                            {"value": "maintainerCannotBuyPos",                   "label": "Maintainer cannot buy pos"},
                            {"value": "rolloutPos",                               "label": "Can access instore payment features"},
                            {"value": "displayReportCreator",                     "label": "Display report creator"},
                            {"value": "displayDisputesReport",                    "label": "Display disputes report"},
                            {"value": "displayFraudulentTransactionsReport",      "label": "Display fraudulent transactions report"},
                            {"value": "cannotAddNewPaymentTerminal",              "label": "Cannot add new payment terminal"},
                            {"value": "displayThirdPartyAcquiring",               "label": "Display third party acquiring"},
                        ],
                    },
                ],
            },
            {
                "file":        "channel_creator.py",
                "name":        "Créer Channel MID Payfac Low Risk",
                "description": "Crée un channel MID Payfac Ecom Low Risk pour chaque company_ref.",
                "inputs":      ["channel_accounts.csv (colonnes : id, company_ref)", "session.json"],
                "outputs":     ["results_channel_XXXXXX.csv"],
                "params":      [],
            },
            {
                "file":        "realm_user_adder.py",
                "name":        "1 — Ajouter des utilisateurs au royaume",
                "description": "Invite chaque email du CSV comme utilisateur sur le royaume (change-owner → inviter un nouvel utilisateur).",
                "inputs":      ["input/realm_users.csv (colonne : email)", "session.json"],
                "outputs":     ["results/results_realm_users_XXXXXX.csv"],
                "params": [
                    {
                        "id":          "realm_id",
                        "type":        "text",
                        "label":       "Realm ID",
                        "placeholder": "ex: realm_xxxxxxxxxxxx",
                    },
                ],
            },
            {
                "file":        "realm_owner_setter.py",
                "name":        "2 — Définir le propriétaire d'une entreprise",
                "description": "Pour chaque ligne du CSV, retrouve la company_ref dans le royaume, ouvre 'Changer propriétaire' et sélectionne l'email comme nouveau propriétaire.",
                "inputs":      ["input/realm_owners.csv (colonnes : company_ref, email)", "session.json"],
                "outputs":     ["results/results_realm_owners_XXXXXX.csv"],
                "params": [
                    {
                        "id":          "realm_id",
                        "type":        "text",
                        "label":       "Realm ID",
                        "placeholder": "ex: realm_xxxxxxxxxxxx",
                    },
                ],
            },
            {
                "file":        "bank_transfer_whitelister.py",
                "name":        "Whitelist date de transfer status",
                "description": "Pour chaque ID du CSV, ouvre la fiche transfer, sélectionne 'whitelisted' et applique la date saisie.",
                "inputs":      ["input/bank_transfer_ids.csv (colonne : id)", "session.json"],
                "outputs":     ["results/results_whitelist_XXXXXX.csv"],
                "params": [
                    {
                        "id":          "date",
                        "type":        "text",
                        "label":       "Date Whitelist (AAAA-MM-JJ)",
                        "placeholder": "ex: 2026-12-31",
                    },
                ],
            },
        ],
        "session_check":   {"url": "https://admin.payplug.com/admin/companies", "login_patterns": ["login"], "body_patterns": ['name="password"', 'name="email"']},
        "results_globs":   ["results/results_*.csv", "results/company_refs_*.csv", "results/raison_sociale_*.csv", "results/siret_*.csv", "results/results_channel_*.csv", "results/results_features_*.csv", "results/results_realm_users_*.csv", "results/results_realm_owners_*.csv"],
        "editable_csvs":   [
            {"file": "input/data.csv",                 "label": "Data.csv",                 "format": "id, url",            "color": "#3b82f6"},
            {"file": "input/channel_accounts.csv",     "label": "Channel accounts.csv",     "format": "id, company_ref",    "color": "#a855f7"},
            {"file": "input/realm_users.csv",          "label": "Realm users.csv",          "format": "email",              "color": "#14b8a6"},
            {"file": "input/realm_owners.csv",         "label": "Realm owners.csv",         "format": "company_ref, email", "color": "#f97316"},
            {"file": "input/bank_transfer_ids.csv",    "label": "Bank transfer IDs.csv",    "format": "id",                 "color": "#f43f5e"},
        ],
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
                "file":        "create_customers.py",
                "name":        "1 — Créer les Customers",
                "description": "Scrape les fiches admin (raison sociale, company_ref, adresse, TVA…) puis crée le customer dans Solvimon via POST /customers.",
                "inputs":      ["input/admin_ids.csv (colonne : id)", "admin_payplug/session.json"],
                "outputs":     ["results/results_create_customers_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "env",
                        "type":    "radio",
                        "label":   "Environnement",
                        "options": [
                            {"value": "prod",    "label": "Production — payplug.solvimon.com"},
                            {"value": "sandbox", "label": "Sandbox — test.api.solvimon.com"},
                        ],
                        "default": "prod",
                    },
                ],
            },
            {
                "file":        "inspect_subscription.py",
                "name":        "2 — Vérifier la Subscription",
                "description": "Vérifie que la subscription source existe avant de lancer la duplication.",
                "inputs":      [],
                "outputs":     [],
                "params": [
                    {
                        "id":      "env",
                        "type":    "radio",
                        "label":   "Environnement",
                        "options": [
                            {"value": "prod",    "label": "Production — payplug.solvimon.com"},
                            {"value": "sandbox", "label": "Sandbox — test.api.solvimon.com"},
                        ],
                        "default": "prod",
                    },
                    {
                        "id":          "sub_id",
                        "type":        "text",
                        "label":       "Subscription ID source",
                        "placeholder": "ex: sub_xxxxxxxxxxxxxxxx",
                    },
                ],
            },
            {
                "file":        "solvimon_bulk_subscriptions.py",
                "name":        "3 — Créer les Subscriptions",
                "description": "POST /copy → PATCH customer_id + ACTIVE pour chaque ligne du CSV.",
                "inputs":      ["customers.csv (colonne : customer_id)"],
                "outputs":     ["results/results_solvimon_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "env",
                        "type":    "radio",
                        "label":   "Environnement",
                        "options": [
                            {"value": "prod",    "label": "Production — payplug.solvimon.com"},
                            {"value": "sandbox", "label": "Sandbox — test.api.solvimon.com"},
                        ],
                        "default": "prod",
                    },
                    {
                        "id":          "sub_id",
                        "type":        "text",
                        "label":       "Subscription ID source",
                        "placeholder": "ex: sub_xxxxxxxxxxxxxxxx",
                    },
                ],
            },
        ],
        "results_globs":    ["results/results_solvimon_*.csv", "results/results_create_customers_*.csv"],
        "editable_csvs":    [
            {"file": "customers.csv",       "label": "Customers.csv",  "format": "customer_id", "color": "#10b981"},
            {"file": "input/admin_ids.csv", "label": "Admin IDs.csv",  "format": "id",          "color": "#6366f1"},
        ],
        "test_mode_script": "solvimon_bulk_subscriptions.py",
    },
    {
        "id":          "cockpit_payplug",
        "name":        "Cockpit PayPlug",
        "subtitle":    "Automatisation Cockpit",
        "description": "Scripts d'automatisation du Cockpit interne : email recovery, mise à jour MID.",
        "color":       "danger",
        "icon":        "bi-shield-lock-fill",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session Cockpit",
                "description": "Sauvegarde les cookies de session Cockpit.",
                "warning":     "Connexion manuelle dans Chromium, sauf si des identifiants sont fournis ci-dessous.",
                "inputs":      [],
                "outputs":     ["session.json"],
                "params": [
                    {
                        "id":    "credentials",
                        "type":  "credentials",
                        "label": "Identifiants de connexion (optionnel)",
                    },
                ],
            },
            {
                "file":        "cockpit_mid_updater.py",
                "name":        "MID Check/Update",
                "description": "Pour chaque ID du CSV, vérifie le SIRET ou met à jour le seuil smartAccepteurCreditThreshold.",
                "inputs":      ["input/data.csv (colonne : id)", "session.json"],
                "outputs":     ["results/results_mid_siret_XXXXXX.csv", "results/results_mid_updater_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "action",
                        "type":    "radio_with_sub",
                        "label":   "Action",
                        "default": "check_siret",
                        "options": [
                            {
                                "value": "check_siret",
                                "label": "SIRET",
                                "desc":  "Vérifie le SIRET de chaque MID",
                            },
                            {
                                "value":       "__sub__",
                                "label":       "Seuil Smart",
                                "desc":        "Analyse ou met à jour le seuil Smart Accepteur Credit",
                                "sub_options": [
                                    {"value": "analyse", "label": "Lecture seule"},
                                    {"value": "1",       "label": "1"},
                                    {"value": "20",      "label": "20"},
                                    {"value": "400",     "label": "400"},
                                ],
                            },
                        ],
                    },
                ],
            },
            {
                "file":        "cockpit_identifier.py",
                "name":        "Lancer le bot",
                "description": "Pour chaque ID du CSV, soumet l'email de récupération.",
                "inputs":      ["input/data.csv (colonne : id)", "session.json"],
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
        "session_check":    {"url": None},
        "results_globs":    ["results/results_cockpit_*.csv", "results/results_mid_updater_*.csv", "results/results_mid_siret_*.csv"],
        "editable_csvs":    [{"file": "input/data.csv", "label": "Data.csv", "format": "id", "color": "#ef4444"}],
        "test_mode_script": "cockpit_identifier.py",
    },
    {
        "id":          "portal_payplug",
        "name":        "Portail PayPlug",
        "subtitle":    "Administration du portail",
        "description": "Scripts d'administration du portail PayPlug : session, génération de clés API.",
        "color":       "warning",
        "icon":        "bi-key-fill",
        "scripts": [
            {
                "file":        "login.py",
                "name":        "Sauvegarder la session PayPlug",
                "description": "Sauvegarde les cookies de session PayPlug.",
                "warning":     "Connexion manuelle dans Chromium, sauf si des identifiants sont fournis ci-dessous.",
                "inputs":      [],
                "outputs":     ["session.json"],
                "params": [
                    {
                        "id":    "credentials",
                        "type":  "credentials",
                        "label": "Identifiants de connexion (optionnel)",
                    },
                ],
            },
            {
                "file":        "keygen.py",
                "name":        "Générer les Clés API",
                "description": "Switch compte → génère clé OAuth2 → JWT → company_ref.",
                "inputs":      ["keygen_accounts.csv (company_ref, account_name, key_name)", "session.json"],
                "outputs":     ["results/results_payplug_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "key_type",
                        "type":    "radio",
                        "label":   "Type de clé",
                        "options": [
                            {"value": "1", "label": "OAuth2 — Client ID + Secret"},
                            {"value": "2", "label": "API Key — clé unique"},
                        ],
                        "default": "1",
                    },
                    {
                        "id":      "environment",
                        "type":    "radio",
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
                "file":        "notification_updater.py",
                "name":        "Activer/Désactiver les notifications",
                "description": "Switch compte → active ou désactive les notifications email sélectionnées.",
                "inputs":      ["input/bank_transfer_accounts.csv (company_ref, account_name)", "session.json"],
                "outputs":     ["results/results_notifications_XXXXXX.csv"],
                "params": [
                    {
                        "id":      "action",
                        "type":    "radio",
                        "label":   "Action",
                        "options": [
                            {"value": "activate",   "label": "Activer"},
                            {"value": "deactivate", "label": "Désactiver"},
                        ],
                        "default": "activate",
                    },
                    {
                        "id":    "notifications",
                        "type":  "checkbox_group",
                        "label": "Notifications",
                        "options": [
                            {"value": "notifications-customer-email-payment-confirmations",        "label": "Confirmations de paiement"},
                            {"value": "notifications-customer-email-refund-confirmations",         "label": "Confirmations de remboursement"},
                            {"value": "notifications-merchant-email-successful-payments",          "label": "Paiements réussis"},
                            {"value": "notifications-merchant-email-transfers-requested",          "label": "Demandes de virement"},
                            {"value": "notifications-merchant-email-chargebacks",                  "label": "Oppositions de paiements"},
                            {"value": "notifications-merchant-email-server-notification-failures", "label": "Échecs de notifications serveurs"},
                        ],
                    },
                ],
            },
            {
                "file":        "bank_transfer_checker.py",
                "name":        "Vérifier / Activer les Virements",
                "description": "Vérifie l'état des virements automatiques et les active si inactifs.",
                "inputs":      ["bank_transfer_accounts.csv (company_ref, account_name)", "session.json"],
                "outputs":     ["results/results_bank_transfer_XXXXXX.csv"],
                "params":      [],
            },
        ],
        "session_check":    {"url": "https://portal.payplug.com/api/v1/user", "login_patterns": ["login", "401"], "body_patterns": ['type="password"']},
        "results_globs":    ["results/results_payplug_*.csv", "results/results_bank_transfer_*.csv", "results/results_notifications_*.csv"],
        "editable_csvs":    [
            {"file": "input/keygen_accounts.csv",        "label": "Keygen accounts.csv",        "format": "company_ref, account_name, key_name", "color": "#f59e0b"},
            {"file": "input/bank_transfer_accounts.csv", "label": "Bank transfer accounts.csv", "format": "company_ref, account_name",           "color": "#ec4899"},
        ],
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
                encoding="utf-8",
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
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
    value    = request.json.get("test_mode", True)
    bot_path = BASE_DIR / bot_id
    # Met à jour TEST_MODE dans tous les scripts Python du bot
    for py_file in bot_path.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if re.search(r'TEST_MODE\s*=\s*(True|False)', content):
            new = re.sub(r'TEST_MODE\s*=\s*(True|False)', f'TEST_MODE = {str(value)}', content)
            py_file.write_text(new, encoding="utf-8")
    return jsonify({"ok": True})


@app.route("/api/csv/<bot_id>/<path:filename>")
def read_csv(bot_id, filename):
    path = BASE_DIR / bot_id / filename
    if not path.exists():
        return jsonify({"content": ""}), 200
    return jsonify({"content": path.read_text(encoding="utf-8")})


@app.route("/api/csv/<bot_id>/<path:filename>", methods=["POST"])
def write_csv(bot_id, filename):
    content = request.json.get("content", "")
    path    = BASE_DIR / bot_id / filename
    path.write_text(content, encoding="utf-8")
    return jsonify({"ok": True})


def _credentials_path(bot_id):
    return BASE_DIR / bot_id / "credentials.json"


def _load_credentials(bot_id):
    path = _credentials_path(bot_id)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.route("/api/credentials/<bot_id>")
def get_credentials(bot_id):
    """Liste les identifiants enregistrés (nom + login + mot de passe — usage local uniquement)."""
    return jsonify(_load_credentials(bot_id))


@app.route("/api/credentials/<bot_id>", methods=["POST"])
def save_credentials(bot_id):
    data     = request.json or {}
    name     = (data.get("name") or "").strip()
    login    = (data.get("login") or "").strip()
    password = (data.get("password") or "").strip()
    if not name or not login or not password:
        return jsonify({"ok": False, "error": "Nom, identifiant et mot de passe requis"}), 400

    creds = _load_credentials(bot_id)
    creds[name] = {"login": login, "password": password}
    with open(_credentials_path(bot_id), "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


@app.route("/api/credentials/<bot_id>/<name>", methods=["DELETE"])
def delete_credentials(bot_id, name):
    creds = _load_credentials(bot_id)
    creds.pop(name, None)
    with open(_credentials_path(bot_id), "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


@app.route("/api/session-owner/<bot_id>")
def get_session_owner(bot_id):
    """Nom des identifiants associés à la session active (None si connexion manuelle)."""
    path = BASE_DIR / bot_id / "session_owner.json"
    if not path.exists():
        return jsonify({"name": None})
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/session-check/<bot_id>")
def session_check(bot_id):
    bot = next((b for b in BOTS if b["id"] == bot_id), None)
    if not bot:
        return jsonify({"active": None})

    check_cfg = bot.get("session_check")
    if not check_cfg:
        return jsonify({"active": None})

    session_file = BASE_DIR / bot_id / "session.json"
    if not session_file.exists():
        return jsonify({"active": False, "reason": "session.json introuvable"})

    with open(session_file, encoding="utf-8") as f:
        storage = json.load(f)

    now = time.time()
    cookies = {}
    for cookie in storage.get("cookies", []):
        exp = cookie.get("expires", -1)
        if exp == -1 or exp > now:
            cookies[cookie["name"]] = cookie["value"]

    # Pas de cookies → session non vérifiable (ex : SPA avec tokens en sessionStorage)
    if not cookies:
        return jsonify({"active": None, "reason": "Pas de cookies dans la session"})

    url = check_cfg.get("url")
    if not url:
        return jsonify({"active": None, "reason": "Pas d'URL de vérification"})

    login_patterns = check_cfg.get("login_patterns", ["login"])
    body_patterns  = check_cfg.get("body_patterns",  ['type="password"'])

    try:
        r         = req_lib.get(url, cookies=cookies, timeout=8, allow_redirects=True, verify=False)
        final_url = r.url.lower()
        is_login  = (
            r.status_code in (401, 403) or
            any(p in final_url for p in login_patterns) or
            any(p in r.text    for p in body_patterns)
        )
        return jsonify({"active": not is_login})
    except Exception as e:
        return jsonify({"active": False, "reason": str(e)[:100]})


@app.route("/api/company_refs_files/<bot_id>")
def get_company_refs_files(bot_id):
    bot_path = BASE_DIR / bot_id
    files    = sorted(glob(str(bot_path / "results" / "company_refs_*.csv")), reverse=True)
    result   = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                count = sum(1 for _ in fp) - 1  # minus header
        except Exception:
            count = -1
        result.append({"name": Path(f).name, "count": max(count, 0)})
    return jsonify(result)


@app.route("/api/results/<bot_id>")
def get_results(bot_id):
    bot = next((b for b in BOTS if b["id"] == bot_id), None)
    if not bot:
        return jsonify([])

    all_files = []
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
