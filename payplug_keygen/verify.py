"""
Vérifie que chaque clé API est bien associée au bon compte PayPlug
en tentant de créer un planner de test sur le companyRef correspondant.
  → 201 Created  : clé valide et autorisée sur ce compte
  → 403 Forbidden: clé non autorisée (mauvais compte)
  → autre erreur : problème de credentials ou d'API
"""
import requests
import base64
import csv
import time
import pandas as pd
from datetime import datetime, timedelta
from glob import glob
import os

TOKEN_URL    = "https://api.payplug.com/oauth2/token"
REPORTS_BASE = "https://payment.payplug.com"
PLANNERS_URL = f"{REPORTS_BASE}/reports/planners"
DEFS_URL     = f"{REPORTS_BASE}/reports/definitions?format=full"

# ID de la définition Settlement Report.
# Laisser à None pour le récupérer automatiquement via l'API au démarrage.
# Ou forcer manuellement : DEFINITION_ID = "fe12d1a9-2357-45f1-8537-89e0294020d8"
DEFINITION_ID   = None

LOG_FILE        = f"results/verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
RESULTS_FILE    = None      # None = charge automatiquement le results_payplug_*.csv le plus récent
CLEANUP_PLANNER = True      # supprime les planners de test après vérification


# ── Auth ───────────────────────────────────────────────────────────────────────

def get_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
        data={
            "grant_type": "client_credentials",
            "audience":   "https://www.payplug.com",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


# ── Définitions ────────────────────────────────────────────────────────────────

def fetch_definition_id(token: str) -> str:
    r = requests.get(
        DEFS_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    items = data if isinstance(data, list) else data.get("items", data.get("definitions", []))
    for d in items:
        if "settlement" in d.get("name", "").lower():
            return d["id"]
    return items[0]["id"]


# ── Planner ────────────────────────────────────────────────────────────────────

def create_test_planner(token: str, account_id: str, definition_id: str) -> str:
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    exec_date = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "name": f"verify-{account_id[:8]}-{datetime.now().strftime('%H%M%S')}",
        "request": {
            "definitionId":     definition_id,
            "timezone":         "Europe/Paris",
            "companyRefs":      [account_id],
            "filteringCriteria": {
                "transferDate": {
                    "operator": "between",
                    "after":    f"{yesterday}T00:00:00Z",
                    "before":   f"{yesterday}T23:59:59Z",
                }
            },
            "columns": ["companyName", "operationDate", "operationAmount", "transferDate"],
        },
        "singleExecutionDate": exec_date,
    }

    r = requests.post(
        PLANNERS_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        timeout=10,
    )
    r.raise_for_status()
    location   = r.headers.get("Location", "")
    planner_id = location.rstrip("/").split("/")[-1]
    return planner_id


def delete_planner(token: str, planner_id: str):
    requests.delete(
        f"{PLANNERS_URL}/{planner_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def find_latest_results() -> str:
    files = sorted(glob("results/results_payplug_*.csv"), reverse=True)
    if not files:
        raise FileNotFoundError("Aucun fichier results_payplug_*.csv trouvé dans results/")
    return files[0]


def main():
    os.makedirs("results", exist_ok=True)

    source = RESULTS_FILE or find_latest_results()
    print(f"Fichier source : {source}\n")

    df = pd.read_csv(source, dtype=str).fillna("")
    df = df[df["status"] == "OK"]
    print(f"{len(df)} compte(s) à vérifier.\n")

    definition_id = DEFINITION_ID
    verif_results = []

    for _, row in df.iterrows():
        account_id    = row["account_id"]
        account_name  = row.get("account_name", account_id)
        client_id     = row["client_id"]
        client_secret = row["client_secret"]

        print(f"→ [{account_name}] {account_id}")

        result = {
            "account_id":   account_id,
            "account_name": account_name,
            "client_id":    client_id,
            "token_ok":     "",
            "planner_ok":   "",
            "planner_id":   "",
            "verdict":      "",
            "status":       "",
            "message":      "",
        }

        try:
            # Étape 1 — Token OAuth2
            token = get_token(client_id, client_secret)
            result["token_ok"] = "OUI"
            print(f"  ✓ Token obtenu")

            # Étape 2 — Definition ID (une seule fois pour tous les comptes)
            if definition_id is None:
                definition_id = fetch_definition_id(token)
                print(f"  ✓ Definition ID : {definition_id}")

            # Étape 3 — Créer le planner de test
            planner_id = create_test_planner(token, account_id, definition_id)
            result["planner_ok"] = "OUI"
            result["planner_id"] = planner_id
            result["verdict"]    = "CLÉ VALIDE — COMPTE ASSOCIÉ"
            result["status"]     = "OK"
            print(f"  ✓ Planner créé : {planner_id}")
            print(f"  ✓ VERDICT : clé bien associée au compte")

            # Étape 4 — Nettoyage
            if CLEANUP_PLANNER and planner_id:
                delete_planner(token, planner_id)
                print(f"  ✓ Planner de test supprimé")

        except requests.HTTPError as e:
            code = e.response.status_code
            msg  = e.response.text[:200]
            result["message"] = msg

            if result["token_ok"] != "OUI":
                result["token_ok"] = "NON"
                result["status"]   = f"ERREUR_TOKEN_{code}"
                result["verdict"]  = "CREDENTIALS INVALIDES"
                print(f"  ✗ Token refusé — HTTP {code}")
            elif code == 403:
                result["planner_ok"] = "NON"
                result["status"]     = "ERREUR_PLANNER_403"
                result["verdict"]    = "CLÉ NON AUTORISÉE SUR CE COMPTE"
                print(f"  ✗ 403 Accès refusé → clé NON associée à ce compte")
            else:
                result["planner_ok"] = "NON"
                result["status"]     = f"ERREUR_PLANNER_{code}"
                result["verdict"]    = f"ERREUR HTTP {code}"
                print(f"  ✗ HTTP {code} : {e.response.text[:80]}")

        except Exception as e:
            result["status"]  = "ERREUR"
            result["verdict"] = "ERREUR INATTENDUE"
            result["message"] = str(e)[:200]
            print(f"  ✗ {str(e)[:80]}")

        verif_results.append(result)
        time.sleep(0.5)

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "account_id", "account_name", "client_id",
            "token_ok", "planner_ok", "planner_id",
            "verdict", "status", "message",
        ])
        writer.writeheader()
        writer.writerows(verif_results)

    ok  = sum(1 for r in verif_results if r["status"] == "OK")
    err = len(verif_results) - ok
    print(f"\n─────────────────────────────────────────────")
    print(f"✓ Clés valides et bien associées : {ok}")
    print(f"✗ Erreurs / accès refusés        : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    main()
