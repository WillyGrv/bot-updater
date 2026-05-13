import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",
    "sandbox": "https://test.api.solvimon.com/v1",
}

API_KEY = os.getenv("SOLVIMON_API_KEY", "YOUR_API_KEY_HERE")


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
    return base_url, sub_id


def main():
    base_url, sub_id = ask_config()

    print("─── Appel API ────────────────────────────────")
    r = requests.get(
        f"{base_url}/pricing-plan-subscriptions/{sub_id}",
        headers={"X-API-KEY": API_KEY},
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
