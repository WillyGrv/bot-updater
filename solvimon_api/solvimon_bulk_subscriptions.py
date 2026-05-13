import os
import sys
import requests
import csv
import uuid
import time
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
API_KEY = os.getenv("SOLVIMON_API_KEY", "YOUR_API_KEY_HERE")

ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",
    "sandbox": "https://test.api.solvimon.com/v1",
}

DATA_SOURCE   = "customers.csv"
LOG_FILE      = f"results/results_solvimon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE     = False
DELAY_BETWEEN = 0.5
# ───────────────────────────────────────────────────────────────────────────────


def ask_config():
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        env    = input("  Environnement [prod/sandbox] : ").strip().lower()
        sub_id = input("  Subscription ID source : ").strip()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            env    = input().strip().lower()
            sub_id = input().strip()
        except EOFError:
            print("⚠ Paramètres manquants.")
            sys.exit(1)

    base_url = ENVS.get(env)
    if not base_url:
        print(f"⚠ Environnement inconnu : '{env}' — utilise 'prod' ou 'sandbox'.")
        sys.exit(1)

    print(f"  Environnement : {env.upper()} → {base_url}")
    print(f"  Subscription  : {sub_id}\n")
    return base_url, sub_id


def verify_subscription(base_url: str, sub_id: str, headers: dict) -> bool:
    print("─── Vérification de la subscription source ───")
    r = requests.get(
        f"{base_url}/pricing-plan-subscriptions/{sub_id}",
        headers=headers,
    )
    if r.status_code == 200:
        data = r.json()
        print(f"  ✓ Trouvée — status: {data.get('status', '?')} | customer: {data.get('customer_id', '?')}")
        return True
    else:
        print(f"  ✗ Subscription introuvable (HTTP {r.status_code})")
        try:
            print(f"    {json.dumps(r.json(), ensure_ascii=False)}")
        except Exception:
            print(f"    {r.text[:200]}")
        return False


def copy_subscription(base_url: str, source_id: str, customer_id: str, headers: dict) -> str:
    url = f"{base_url}/pricing-plan-subscriptions/{source_id}/copy"
    r = requests.post(
        url,
        json={"reference": f"BESSON-{customer_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    r.raise_for_status()
    new_id = r.json()["id"]
    print(f"  ✓ Copie créée : {new_id}")
    return new_id


def assign_customer(base_url: str, subscription_id: str, customer_id: str, headers: dict) -> dict:
    url = f"{base_url}/pricing-plan-subscriptions/{subscription_id}"
    r = requests.patch(url, json={"customer_id": customer_id, "status": "ACTIVE"}, headers=headers)
    r.raise_for_status()
    print(f"  ✓ Customer assigné : {customer_id}")
    return r.json()


def main():
    base_url, source_sub_id = ask_config()

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": API_KEY,
    }

    if not verify_subscription(base_url, source_sub_id, headers):
        print("\n⚠ Arrêt — subscription source invalide.")
        sys.exit(1)

    print()
    print("Chargement du CSV customers...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seul customer traité.\n")
    else:
        print(f"[PROD] {len(df)} customers à traiter.\n")

    results = []

    for i, row in df.iterrows():
        customer_id = row["customer_id"]
        print(f"[{i+1}/{len(df)}] → Customer {customer_id}")

        result = {
            "customer_id":     customer_id,
            "status":          "",
            "subscription_id": "",
            "message":         "",
        }

        try:
            new_id = copy_subscription(base_url, source_sub_id, customer_id, headers)
            final  = assign_customer(base_url, new_id, customer_id, headers)
            result["status"]          = "OK"
            result["subscription_id"] = final.get("id", new_id)

        except requests.HTTPError as e:
            result["status"]  = f"ERREUR_HTTP_{e.response.status_code}"
            result["message"] = e.response.text[:200]
            print(f"  ✗ HTTP {e.response.status_code} : {e.response.text[:100]}")

        except Exception as e:
            result["status"]  = "ERREUR"
            result["message"] = str(e)[:200]
            print(f"  ✗ {e}")

        results.append(result)
        time.sleep(DELAY_BETWEEN)

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["customer_id", "status", "subscription_id", "message"]
        )
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    main()
