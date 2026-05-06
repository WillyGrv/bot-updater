import os
import requests
import csv
import uuid
import time
import pandas as pd
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
API_KEY = os.getenv("SOLVIMON_API_KEY", "YOUR_API_KEY_HERE")

BASE_URL = "https://test.api.solvimon.com/v1"
# Production : BASE_URL = "https://api.solvimon.com/v1"

SOURCE_SUBSCRIPTION_ID = os.getenv("SOLVIMON_SUB_ID", "YOUR_SUBSCRIPTION_ID_HERE")

DATA_SOURCE   = "customers.csv"
LOG_FILE      = f"results/results_solvimon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE     = False
DELAY_BETWEEN = 0.5
# ───────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "Content-Type": "application/json",
    "X-API-KEY": API_KEY,
}


def copy_subscription(source_id: str, customer_id: str) -> str:
    """Étape 1 — Copie exacte de la subscription source. Retourne le nouvel ID."""
    url = f"{BASE_URL}/pricing-plan-subscriptions/{source_id}/copy"
    r = requests.post(
        url,
        json={"reference": f"BESSON-{customer_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"},
        headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    r.raise_for_status()
    new_id = r.json()["id"]
    print(f"  ✓ Copie créée : {new_id}")
    return new_id


def assign_customer(subscription_id: str, customer_id: str) -> dict:
    """Étape 2 — Assigne la copie au customer cible via PATCH."""
    url = f"{BASE_URL}/pricing-plan-subscriptions/{subscription_id}"
    r = requests.patch(url, json={"customer_id": customer_id, "status": "ACTIVE"}, headers=HEADERS)
    r.raise_for_status()
    print(f"  ✓ Customer assigné : {customer_id}")
    return r.json()


def main():
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
            new_id  = copy_subscription(SOURCE_SUBSCRIPTION_ID, customer_id)
            final   = assign_customer(new_id, customer_id)
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
