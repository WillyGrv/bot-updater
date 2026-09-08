import os
import sys
import csv
import time
import json
from datetime import datetime
from glob import glob
from pathlib import Path

try:
    import requests
    import pandas as pd
    from dotenv import load_dotenv
except ModuleNotFoundError as e:
    print(f"⚠ Dépendance manquante ({e.name}) — le venv du projet n'est pas activé.")
    print("  Depuis la racine du repo : source venv/bin/activate")
    sys.exit(1)

load_dotenv(Path(__file__).parent.parent / ".env")

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
API_KEYS = {
    "prod":    os.getenv("SOLVIMON_API_KEY_PROD", ""),
    "sandbox": os.getenv("SOLVIMON_API_KEY_SANDBOX", ""),
}

ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",
    "sandbox": "https://test.api.solvimon.com/v1",
}

DATA_SOURCE   = "customers.csv"
LOG_FILE      = f"results/results_solvimon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE     = False
DELAY_BETWEEN = 0.5
MAX_RETRIES   = 3
# ───────────────────────────────────────────────────────────────────────────────


def get_create_customers_files() -> list[str]:
    return sorted(glob("results/results_create_customers_*.csv"), reverse=True)


def _request_with_retry(method: str, url: str, max_retries: int = MAX_RETRIES, **kwargs):
    """Requête HTTP avec timeout + retry/backoff exponentiel sur 429, 5xx et erreurs réseau."""
    kwargs.setdefault("timeout", 20)
    for attempt in range(max_retries + 1):
        try:
            r = requests.request(method, url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == max_retries:
                raise
            wait = 2 ** attempt
            print(f"  ⏳ {type(e).__name__} — retry dans {wait}s ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == max_retries:
                return r  # dernier essai épuisé — l'appelant gère l'erreur via raise_for_status()
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else (2 ** attempt)
            print(f"  ⏳ HTTP {r.status_code} — retry dans {wait:.0f}s ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        return r
    return r


def ask_config():
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        env     = input("  Environnement [prod/sandbox] : ").strip().lower()
        sub_id  = input("  Subscription ID source : ").strip()
        dry_raw = input("  DRY_RUN ? (o/N) : ").strip().lower()

        print("\n  Source des customers :")
        print("    [0] customers.csv (manuel)")
        print("    [1] Résultats récents — Créer les Customers")
        src_choice = input("  Choix [0] : ").strip()
        if src_choice == "1":
            files = get_create_customers_files()
            if files:
                print(f"\n  Fichiers disponibles :")
                for i, f in enumerate(files):
                    marker = " ← dernier" if i == 0 else ""
                    print(f"    [{i}] {f}{marker}")
                choice = input(f"\n  Numéro du fichier [Entrée = dernier] : ").strip()
                source_file = files[int(choice)] if choice.isdigit() and int(choice) < len(files) else files[0]
                source_mode = "create_customers_output"
            else:
                print("  ⚠ Aucun fichier trouvé — retour sur customers.csv.")
                source_mode, source_file = "csv_file", ""
        else:
            source_mode, source_file = "csv_file", ""
        print("─────────────────────────────────────────────\n")
    else:
        try:
            env         = input().strip().lower()
            sub_id      = input().strip()
            dry_raw     = input().strip().lower()
            source_mode = input().strip() or "csv_file"
            source_file = input().strip()
        except EOFError:
            print("⚠ Paramètres manquants.")
            sys.exit(1)

    base_url = ENVS.get(env)
    if not base_url:
        print(f"⚠ Environnement inconnu : '{env}' — utilise 'prod' ou 'sandbox'.")
        sys.exit(1)

    env_override = os.environ.get("DRY_RUN", "").strip().lower()
    if env_override in ("1", "true", "yes", "oui"):
        dry_run = True
    elif env_override in ("0", "false", "no", "non"):
        dry_run = False
    else:
        dry_run = dry_raw in ("o", "oui", "y", "yes", "1", "true")

    if source_mode == "create_customers_output" and not source_file:
        files       = get_create_customers_files()
        source_file = files[0] if files else ""

    # Le <select> du dashboard renvoie un nom de fichier nu (sans "results/") — on le
    # reconstruit ici pour que le chemin soit valide quel que soit l'appelant.
    if source_mode == "create_customers_output" and source_file and "/" not in source_file:
        source_file = f"results/{source_file}"

    print(f"  Environnement : {env.upper()} → {base_url}")
    print(f"  Subscription  : {sub_id}")
    print(f"  DRY_RUN       : {dry_run}")
    print(f"  Source        : {source_mode}" + (f" ({source_file})" if source_file else "") + "\n")
    return base_url, sub_id, dry_run, source_mode, source_file, env


def _fetch_customer_name(base_url: str, customer_id: str, headers: dict) -> str:
    """Résout le nom d'affichage d'un customer (organization.legal_name) — best effort."""
    if not customer_id:
        return ""
    try:
        r = _request_with_retry("GET", f"{base_url}/customers/{customer_id}", headers=headers, max_retries=1)
        if r.status_code != 200:
            return ""
        data = r.json()
        org  = data.get("organization") or {}
        return org.get("legal_name") or data.get("email") or ""
    except Exception:
        return ""


def _fetch_plan_name(base_url: str, plan_id: str, headers: dict) -> str:
    """Résout le nom d'affichage d'un pricing plan — best effort."""
    if not plan_id:
        return ""
    try:
        r = _request_with_retry("GET", f"{base_url}/pricing-plans/{plan_id}", headers=headers, max_retries=1)
        if r.status_code != 200:
            return ""
        return r.json().get("name") or ""
    except Exception:
        return ""


def verify_subscription(base_url: str, sub_id: str, headers: dict) -> bool:
    print("─── Vérification de la subscription source ───")
    r = _request_with_retry(
        "GET",
        f"{base_url}/pricing-plan-subscriptions/{sub_id}",
        headers=headers,
    )
    if r.status_code == 200:
        data        = r.json()
        customer_id = data.get("customer_id", "")
        plan_id     = data.get("pricing_plan_id", "")

        customer_name = _fetch_customer_name(base_url, customer_id, headers)
        plan_name     = _fetch_plan_name(base_url, plan_id, headers)

        customer_label = f"{customer_name} ({customer_id})" if customer_name else (customer_id or "?")
        plan_label     = f"{plan_name} ({plan_id})" if plan_name else (plan_id or "?")

        print(f"  ✓ Trouvée — status: {data.get('status', '?')} | customer : {customer_label} | offre : {plan_label}")
        return True
    else:
        print(f"  ✗ Subscription introuvable (HTTP {r.status_code})")
        try:
            print(f"    {json.dumps(r.json(), ensure_ascii=False)}")
        except Exception:
            print(f"    {r.text[:200]}")
        return False


def confirm_run(dry_run: bool, customer_count: int) -> None:
    """Demande une confirmation explicite avant d'envoyer le run à l'ensemble des customers (sautée en DRY_RUN)."""
    if dry_run:
        return

    print(f"\n⚠ {customer_count} client(s) vont recevoir une copie de cet abonnement et être activés.")
    # Marqueur lu par le dashboard pour afficher un bouton de confirmation Oui/Non
    # (le pipe stdin de ce script reste ouvert côté dashboard — voir "interactive" dans BOTS).
    print("###CONFIRM_REQUIRED###")
    try:
        typed = input("  Confirmer et lancer le run réel ? (o/N) : ").strip().lower()
    except EOFError:
        print("  (mode non-interactif — confirmation impossible ici, poursuite du run)")
        return

    if typed not in ("o", "oui", "y", "yes"):
        print("⚠ Annulé — arrêt sans rien faire.")
        sys.exit(1)

    print(f"✓ Confirmé — envoi vers {customer_count} client(s).\n")


def load_customer_rows(source_mode: str, source_file: str) -> list[dict]:
    """Retourne une liste de {'id': ..., 'customer_id': ...} depuis customers.csv ou un
    fichier de résultats 'Créer les Customers' (colonnes A=company_id, G=solvimon_id)."""
    if source_mode == "create_customers_output":
        if not source_file or not os.path.exists(source_file):
            print(f"⚠ Fichier introuvable : {source_file}")
            return []
        rows = []
        with open(source_file, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "OK" and row.get("solvimon_id") and row.get("company_id"):
                    rows.append({"id": row["company_id"], "customer_id": row["solvimon_id"]})
        return rows

    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id" not in df.columns or "customer_id" not in df.columns:
        print("⚠ Colonnes 'id' (ID du compte input) et 'customer_id' requises dans customers.csv — arrêt.")
        return []

    df = df.dropna(subset=["id", "customer_id"])
    return df.to_dict("records")


def copy_subscription(base_url: str, source_id: str, account_id: str, customer_id: str, headers: dict, dry_run: bool = False) -> str:
    url       = f"{base_url}/pricing-plan-subscriptions/{source_id}/copy"
    reference = f"{account_id}-{customer_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    # Clé stable (source + customer) : un retry après timeout réutilise la même clé
    # au lieu d'en générer une nouvelle, ce qui évite de copier l'abonnement en double.
    idempotency_key = f"copy-{source_id}-{customer_id}"

    if dry_run:
        print(f"  ⏭ SIMULÉ (DRY_RUN) — copie de {source_id} → référence '{reference}'")
        return f"dry-run-{customer_id}"

    r = _request_with_retry(
        "POST",
        url,
        json={"reference": reference},
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    r.raise_for_status()
    new_id = r.json()["id"]
    print(f"  ✓ Copie créée : {new_id}")
    return new_id


def assign_customer(base_url: str, subscription_id: str, customer_id: str, headers: dict, dry_run: bool = False) -> dict:
    if dry_run:
        print(f"  ⏭ SIMULÉ (DRY_RUN) — assignation customer {customer_id} → {subscription_id} (non activée)")
        return {"id": subscription_id, "dry_run": True}

    url = f"{base_url}/pricing-plan-subscriptions/{subscription_id}"
    r = _request_with_retry("PATCH", url, json={"customer_id": customer_id, "status": "ACTIVE"}, headers=headers)
    r.raise_for_status()
    print(f"  ✓ Customer assigné : {customer_id}")
    return r.json()


def main():
    base_url, source_sub_id, dry_run, source_mode, source_file, env = ask_config()

    api_key = API_KEYS.get(env, "")
    if not api_key:
        print(f"⚠ Clé API Solvimon manquante pour l'environnement '{env}' — renseigne SOLVIMON_API_KEY_{env.upper()} dans .env.")
        sys.exit(1)

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": api_key,
    }

    if not verify_subscription(base_url, source_sub_id, headers):
        print("\n⚠ Arrêt — subscription source invalide.")
        sys.exit(1)

    print()
    if source_mode == "create_customers_output":
        print(f"Chargement de {source_file}...")
    else:
        print("Chargement du CSV customers...")
    rows = load_customer_rows(source_mode, source_file)

    if not rows:
        print("⚠ Aucune ligne valide à traiter — arrêt.")
        return

    if TEST_MODE:
        rows = rows[:1]
        print(f"[MODE TEST] 1 seul customer traité.\n")
    else:
        print(f"[PROD] {len(rows)} customers à traiter.\n")

    confirm_run(dry_run, len(rows))

    results = []

    for i, row in enumerate(rows):
        account_id  = row["id"]
        customer_id = row["customer_id"]
        print(f"[{i+1}/{len(rows)}] → Compte {account_id} | Customer {customer_id}")

        result = {
            "id":              account_id,
            "customer_id":     customer_id,
            "status":          "",
            "subscription_id": "",
            "message":         "",
        }

        try:
            new_id = copy_subscription(base_url, source_sub_id, account_id, customer_id, headers, dry_run)
            final  = assign_customer(base_url, new_id, customer_id, headers, dry_run)
            result["status"]          = "OK"
            result["subscription_id"] = final.get("id", new_id)
            if dry_run:
                result["message"] = "simulé (dry_run)"

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
            f, fieldnames=["id", "customer_id", "status", "subscription_id", "message"]
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
