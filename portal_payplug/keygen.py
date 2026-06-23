import asyncio
import base64
import csv
import json
import os
import sys
import requests
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────────────────────────
DATA_SOURCE  = "input/keygen_accounts.csv"
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
SEL_CANCEL_BTN      = "[data-e2e='api-keys-modal-cancel-button']"

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


async def _screenshot_timeout(page, identifier: str) -> bool:
    """Capture screenshot + HTML on timeout. Returns True si la page est blanche."""
    os.makedirs("screenshots", exist_ok=True)
    is_blank = False
    try:
        path_png = f"screenshots/timeout_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True)
        print(f"  📸 Screenshot → {path_png}")
        print(f"  🌐 URL : {page.url}")
        body_text = await page.evaluate("() => document.body ? document.body.innerText.trim() : ''")
        is_blank  = len(body_text) < 50
        if is_blank:
            print(f"  ⚠ Page blanche détectée")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 2000) : '(vide)'")
        with open(f"screenshots/timeout_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  📄 HTML → screenshots/timeout_{identifier}.html")
    except Exception as dbg_err:
        print(f"  ⚠ Capture debug échouée : {dbg_err}")
    return is_blank


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
    """Retourne (key_type, environment).
    - Terminal (TTY) : prompts interactifs
    - Dashboard/pipe : ligne 1 = key_type (1/2), ligne 2 = environment (1/2)
    """
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        kt  = input("Type de clé   — [1] OAuth2  [2] API Key  (défaut: 1) : ").strip()
        env = input("Environnement — [1] Test    [2] Live     (défaut: 1) : ").strip()
        key_type    = "api_key" if kt  == "2" else "oauth2"
        environment = "live"    if env == "2" else "test"
        print(f"\n→ Type : {key_type.upper()}  |  Env : {environment.upper()}")
        print("─────────────────────────────────────────────\n")
        return key_type, environment
    try:
        kt  = input().strip()
        env = input().strip()
    except EOFError:
        return DEFAULT_KEY_TYPE, DEFAULT_ENVIRONMENT
    key_type    = "api_key" if kt  == "2" else "oauth2"
    environment = "live"    if env == "2" else "test"
    print(f"  ✓ Type : {key_type.upper()}  |  Env : {environment.upper()}\n")
    return key_type, environment


def load_session(path: str) -> dict:
    """Charge la session et s'assure que toutes les valeurs localStorage sont des strings."""
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def switch_account(page, company_ref: str):
    await page.click(ACCOUNT_SWITCHER_TRIGGER)
    # Attendre que le panel soit ouvert (au moins un item présent)
    await page.wait_for_selector("[data-e2e^='account-switcher-company-']", timeout=10000)
    company_sel = f"[data-e2e='account-switcher-company-{company_ref}']"
    locator = page.locator(company_sel)
    try:
        await locator.wait_for(timeout=12000)
    except PlaywrightTimeout:
        # Peut nécessiter un scroll dans le panel
        await page.evaluate("""
            (sel) => {
                const el = document.querySelector(sel);
                if (el) el.scrollIntoView({ block: 'center' });
            }
        """, company_sel)
        await locator.wait_for(timeout=8000)
    await locator.scroll_into_view_if_needed()
    await locator.click()
    await page.wait_for_load_state("domcontentloaded", timeout=15000)


async def generate_key(page, company_ref: str, key_name: str, account_name: str,
                       key_type: str, environment: str) -> dict:
    result = {
        "company_ref":   company_ref,
        "account_name":  account_name,
        "key_name":      key_name,
        "key_type":      key_type,
        "environment":   environment,
        "client_id":     "",
        "client_secret": "",
        "api_key":       "",
        "jwt_token":     "",
        "status":        "",
        "message":       "",
    }

    try:
        # Fermer la modale si elle est restée ouverte après une erreur précédente
        try:
            close_btn = page.locator(SEL_CLOSE_BTN)
            if await close_btn.is_visible(timeout=1500):
                await close_btn.click()
                await page.wait_for_selector(SEL_CLOSE_BTN, state="hidden", timeout=3000)
        except Exception:
            pass

        await switch_account(page, company_ref)
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
            if switched:
                print(f"  ✓ Environnement Live activé")
            # Attendre que le bouton soit stable après le re-render React
            await page.wait_for_selector(SEL_SUBMIT_BTN, state="visible", timeout=8000)
            await asyncio.sleep(0.3)

            # ── Vérifier si la génération Live est bloquée (onboarding non validé) ─
            submit_disabled = await page.evaluate("""
                () => {
                    const btn = document.querySelector('[data-e2e="api-keys-modal-submit-button"]');
                    return btn ? (btn.disabled || btn.getAttribute('aria-disabled') === 'true') : false;
                }
            """)
            if submit_disabled:
                tooltip_text = await page.evaluate("""
                    () => {
                        const el = document.querySelector('.ant-tooltip-inner[role="tooltip"]');
                        return el ? el.textContent.trim()
                                  : "Génération de clé impossible (onboarding non finalisé)";
                    }
                """)
                result["status"]  = "ERREUR_ONBOARDING_INCOMPLET"
                result["message"] = tooltip_text
                print(f"  ✗ Génération Live bloquée — compte ignoré")
                try:
                    cancel_btn = page.locator(SEL_CANCEL_BTN)
                    if await cancel_btn.is_visible(timeout=2000):
                        await cancel_btn.click()
                        await page.wait_for_selector(SEL_CANCEL_BTN, state="hidden", timeout=3000)
                except Exception:
                    pass
                return result

        # ── Générer ────────────────────────────────────────────────────────────
        # Clic JS direct pour contourner les overlays/animations React
        await page.wait_for_selector(SEL_SUBMIT_BTN, state="attached", timeout=8000)
        await page.evaluate(
            "document.querySelector('[data-e2e=\"api-keys-modal-submit-button\"]').click()"
        )
        await asyncio.sleep(1.5)

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
        is_blank = await _screenshot_timeout(page, company_ref)
        if is_blank:
            result["status"] = "ERREUR_PAGE_BLANCHE"

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
            company_ref   = row["company_ref"]
            account_name = row.get("account_name", company_ref)
            key_name     = row["key_name"]
            print(f"\n→ [{account_name}] {company_ref}")

            result = await generate_key(
                page, company_ref, key_name, account_name, key_type, environment
            )
            results.append(result)
            if result["status"] == "ERREUR_PAGE_BLANCHE":
                print(f"\n⛔ Page blanche — session probablement expirée. Arrêt du process.")
                break
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company_ref", "account_name", "key_name",
            "key_type", "environment",
            "client_id", "client_secret", "api_key",
            "jwt_token", "status", "message",
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
