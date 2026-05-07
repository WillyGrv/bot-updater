import asyncio
import base64
import csv
import json
import os
import requests
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
DATA_SOURCE  = "accounts.csv"
SESSION_FILE = "session.json"
KEYS_URL     = "https://portal.payplug.com/#/configuration/connection/keys"
LOG_FILE     = f"results/results_payplug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

ACCOUNT_SWITCHER_TRIGGER = "[data-e2e='account-switcher']"

# Défauts utilisés en mode dashboard (sans stdin)
DEFAULT_KEY_TYPE    = "oauth2"   # "oauth2" ou "api_key"
DEFAULT_ENVIRONMENT = "test"     # "test" ou "live"
# ───────────────────────────────────────────────────────────────────────────────

# ── Sélecteurs ─────────────────────────────────────────────────────────────────
SEL_GENERATE_BTN    = "[data-e2e='api-keys-generate-key-button']"
SEL_KEY_NAME_INPUT  = "[data-e2e='api-keys-modal-input-clientName']"
SEL_SUBMIT_BTN      = "[data-e2e='api-keys-modal-submit-button']"
SEL_CLOSE_BTN       = "[data-e2e='api-keys-modal-close-button']"

# OAuth2
SEL_CLIENT_ID       = "[data-e2e='api-keys-modal-clientId-value']"
SEL_CLIENT_SECRET   = "[data-e2e='api-keys-modal-clientSecret-value']"

# API Key
SEL_RADIO_API_KEY   = "input[data-e2e='api-keys-modal-radio-api_key']"
SEL_API_KEY_VALUE   = "[data-e2e='api-keys-modal-apiKey-value']"

# Environnement (toggle Test / Live) — on clique sur le label wrappeur
SEL_ENV_SWITCH      = "[data-e2e='api-keys-modal-switch'] label"
# ───────────────────────────────────────────────────────────────────────────────


TOKEN_URL = "https://api.payplug.com/oauth2/token"


def get_oauth_token(client_id: str, client_secret: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
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


def decode_jwt_company_ref(token: str) -> tuple[str, dict]:
    """Décode le payload JWT et retourne (company_ref, claims_complets)."""
    try:
        payload_b64 = token.split(".")[1]
        padding     = "=" * (4 - len(payload_b64) % 4)
        claims      = json.loads(base64.b64decode(payload_b64 + padding).decode())
    except Exception as e:
        return f"DECODE_ERROR: {e}", {}

    company_ref = (
        claims.get("company_ref")
        or claims.get("companyRef")
        or claims.get("company_id")
        or claims.get("companyId")
        or claims.get("sub", "")
    )
    return str(company_ref), claims


def ask_config() -> tuple[str, str]:
    """Demande le type de clé et l'environnement au démarrage.
    En mode dashboard (stdin fermé), utilise les valeurs DEFAULT_*."""
    try:
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        kt = input("Type de clé   — [1] OAuth2  [2] API Key  (défaut: 1) : ").strip()
        key_type = "api_key" if kt == "2" else "oauth2"

        env = input("Environnement — [1] Test    [2] Live     (défaut: 1) : ").strip()
        environment = "live" if env == "2" else "test"

        print(f"\n→ Type : {key_type.upper()}  |  Env : {environment.upper()}")
        print("─────────────────────────────────────────────\n")
        return key_type, environment

    except EOFError:
        print(f"Mode dashboard — config par défaut : {DEFAULT_KEY_TYPE.upper()} / {DEFAULT_ENVIRONMENT.upper()}\n")
        return DEFAULT_KEY_TYPE, DEFAULT_ENVIRONMENT


def load_session(path: str) -> dict:
    """Charge la session et s'assure que toutes les valeurs localStorage sont des strings."""
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def switch_account(page, account_id: str):
    await page.click(ACCOUNT_SWITCHER_TRIGGER)
    await page.wait_for_selector(
        f"[data-e2e='account-switcher-company-{account_id}']",
        timeout=8000,
    )
    await page.click(f"[data-e2e='account-switcher-company-{account_id}']")
    await page.wait_for_load_state("domcontentloaded", timeout=15000)


async def generate_key(page, account_id: str, key_name: str, account_name: str,
                       key_type: str, environment: str) -> dict:
    result = {
        "account_id":    account_id,
        "account_name":  account_name,
        "key_name":      key_name,
        "key_type":      key_type,
        "environment":   environment,
        "client_id":     "",
        "client_secret": "",
        "api_key":       "",
        "company_ref":   "",
        "jwt_token":     "",
        "status":        "",
        "message":       "",
    }

    try:
        await switch_account(page, account_id)
        print(f"  ✓ Compte actif : {account_name}")

        await page.goto(KEYS_URL, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(SEL_GENERATE_BTN, timeout=10000)
        await page.click(SEL_GENERATE_BTN)
        await page.wait_for_selector(SEL_KEY_NAME_INPUT, timeout=8000)

        # ── Sélection du type de clé ───────────────────────────────────────────
        if key_type == "api_key":
            await page.click(SEL_RADIO_API_KEY)
            await asyncio.sleep(0.4)
            print(f"  ✓ Mode API Key sélectionné")

        # ── Nom de la clé ──────────────────────────────────────────────────────
        await page.fill(SEL_KEY_NAME_INPUT, key_name)

        # ── Environnement Live ─────────────────────────────────────────────────
        # Le composant React SwitchV2 n'écoute pas les events DOM standards.
        # On passe par evaluate() pour déclencher un vrai click sur le <label>.
        if environment == "live":
            switched = await page.evaluate("""
                () => {
                    const input = document.querySelector(
                        '[data-e2e="api-keys-modal-switch"] input[type="checkbox"]'
                    );
                    if (!input) return false;
                    if (!input.checked) {
                        const label = input.closest('label');
                        if (label) label.click();
                    }
                    return true;
                }
            """)
            await asyncio.sleep(0.4)
            if switched:
                print(f"  ✓ Environnement Live activé")

        # ── Générer ────────────────────────────────────────────────────────────
        await page.click(SEL_SUBMIT_BTN)

        # ── Récupération des credentials ───────────────────────────────────────
        if key_type == "oauth2":
            await page.wait_for_selector(SEL_CLIENT_ID, timeout=10000)
            await page.wait_for_selector(SEL_CLIENT_SECRET, timeout=5000)
            client_id     = (await page.text_content(SEL_CLIENT_ID)).strip()
            client_secret = (await page.text_content(SEL_CLIENT_SECRET)).strip()
            result["client_id"]     = client_id
            result["client_secret"] = client_secret
            print(f"  ✓ OAuth2 — Client ID : {client_id[:8]}...")

            # ── JWT decode → company_ref ───────────────────────────────────────
            try:
                token       = get_oauth_token(client_id, client_secret)
                company_ref, claims = decode_jwt_company_ref(token)
                result["company_ref"] = company_ref
                result["jwt_token"]   = token
                print(f"  ✓ JWT — company_ref : {company_ref}")
                extra = {k: v for k, v in claims.items()
                         if k not in ("iss", "aud", "exp", "iat", "jti", "nbf")}
                if extra:
                    print(f"  ℹ Claims : {extra}")
            except Exception as e:
                result["company_ref"] = f"JWT_ERROR: {str(e)[:80]}"
                print(f"  ⚠ JWT non décodé : {e}")

        else:  # api_key
            await page.wait_for_selector(SEL_API_KEY_VALUE, timeout=10000)
            api_key = (await page.text_content(SEL_API_KEY_VALUE)).strip()
            result["api_key"] = api_key
            print(f"  ✓ API Key — {api_key[:8]}...")

        result["status"] = "OK"

        # ── Fermer la modale ───────────────────────────────────────────────────
        await page.click(SEL_CLOSE_BTN)
        await page.wait_for_selector(SEL_CLOSE_BTN, state="hidden", timeout=5000)

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:150]
        print(f"  ✗ Timeout : {str(e)[:80]}")

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:150]
        print(f"  ✗ {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    # ── Demande de configuration au démarrage ──────────────────────────────────
    key_type, environment = ask_config()

    print("Chargement du CSV comptes...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seul compte traité.\n")
    else:
        print(f"[PROD] {len(df)} comptes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session(SESSION_FILE))
        page    = await context.new_page()

        await page.goto("https://portal.payplug.com/", wait_until="domcontentloaded")

        for _, row in df.iterrows():
            account_id   = row["account_id"]
            account_name = row.get("account_name", account_id)
            key_name     = row["key_name"]
            print(f"\n→ [{account_name}] {account_id}")

            result = await generate_key(
                page, account_id, key_name, account_name, key_type, environment
            )
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "account_id", "account_name", "key_name",
            "key_type", "environment",
            "client_id", "client_secret", "api_key",
            "company_ref", "jwt_token",
            "status", "message",
        ])
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
