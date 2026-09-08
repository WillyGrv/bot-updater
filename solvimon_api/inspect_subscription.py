import os
import sys
import json
import time
from pathlib import Path

try:
    import requests
    from dotenv import load_dotenv
except ModuleNotFoundError as e:
    print(f"⚠ Dépendance manquante ({e.name}) — le venv du projet n'est pas activé.")
    print("  Depuis la racine du repo : source venv/bin/activate")
    sys.exit(1)

load_dotenv(Path(__file__).parent.parent / ".env")

ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",
    "sandbox": "https://test.api.solvimon.com/v1",
}

API_KEYS = {
    "prod":    os.getenv("SOLVIMON_API_KEY_PROD", ""),
    "sandbox": os.getenv("SOLVIMON_API_KEY_SANDBOX", ""),
}
MAX_RETRIES = 3


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
                return r  # dernier essai épuisé — l'appelant gère l'erreur
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
        print("VÉRIFICATION SUBSCRIPTION")
        print("─────────────────────────────────────────────")
        env = input("  Environnement [prod/sandbox] : ").strip().lower()
        sub_id = input("  Subscription ID : ").strip()
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
    return base_url, sub_id, env


def main():
    base_url, sub_id, env = ask_config()

    api_key = API_KEYS.get(env, "")
    if not api_key:
        print(f"⚠ Clé API Solvimon manquante pour l'environnement '{env}' — renseigne SOLVIMON_API_KEY_{env.upper()} dans .env.")
        sys.exit(1)

    print("─── Appel API ────────────────────────────────")
    r = _request_with_retry(
        "GET",
        f"{base_url}/pricing-plan-subscriptions/{sub_id}",
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
    )
    print(f"Status HTTP : {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        print("\n✓ Subscription trouvée :\n")
        for key in ("id", "status", "customer_id", "pricing_plan_id", "reference", "start_date", "end_date"):
            val = data.get(key, "—")
            print(f"  {key:<22} {val}")
        print(f"\n  Réponse complète :")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"\n✗ Subscription introuvable ou erreur API :")
        try:
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(r.text[:500])
        sys.exit(1)


if __name__ == "__main__":
    main()
