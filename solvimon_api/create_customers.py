import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import requests
    import pandas as pd
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    from dotenv import load_dotenv
except ModuleNotFoundError as e:
    print(f"⚠ Dépendance manquante ({e.name}) — le venv du projet n'est pas activé.")
    print("  Depuis la racine du repo : source venv/bin/activate")
    sys.exit(1)

load_dotenv(Path(__file__).parent.parent / ".env")

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE   = "input/admin_ids.csv"
ADMIN_SESSION = Path(__file__).parent.parent / "admin_payplug" / "session.json"
LOG_FILE      = f"results/results_create_customers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE     = False
DELAY_BETWEEN = 1.0

ADMIN_URL = "https://admin.payplug.com/admin/companies/{id}"

ENVS = {
    "prod":    "https://payplug.solvimon.com/v1",
    "sandbox": "https://test.api.solvimon.com/v1",
}

API_KEYS = {
    "prod":    os.getenv("SOLVIMON_API_KEY_PROD", ""),
    "sandbox": os.getenv("SOLVIMON_API_KEY_SANDBOX", ""),
}

REQUIRED_SCRAPED_FIELDS = ["siret", "tva", "email"]
MAX_RETRIES             = 3
# ──────────────────────────────────────────────────────────────────────────────


def ask_config() -> tuple[str, bool]:
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        env     = input("  Environnement [prod/sandbox] : ").strip().lower()
        dry_raw = input("  DRY_RUN ? (o/N) : ").strip().lower()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            env     = input().strip().lower()
            dry_raw = input().strip().lower()
        except EOFError:
            print("⚠ Paramètre manquant.")
            sys.exit(1)

    if env not in ENVS:
        print(f"⚠ Environnement inconnu : '{env}' — utilise 'prod' ou 'sandbox'.")
        sys.exit(1)

    env_override = os.environ.get("DRY_RUN", "").strip().lower()
    if env_override in ("1", "true", "yes", "oui"):
        dry_run = True
    elif env_override in ("0", "false", "no", "non"):
        dry_run = False
    else:
        dry_run = dry_raw in ("o", "oui", "y", "yes", "1", "true")

    print(f"  Environnement : {env.upper()} → {ENVS[env]}")
    print(f"  DRY_RUN       : {dry_run}\n")
    return env, dry_run


def load_session(path) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def _screenshot_timeout(page, identifier: str) -> None:
    os.makedirs("screenshots", exist_ok=True)
    try:
        path_png = f"screenshots/timeout_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True, timeout=15000)
        print(f"  📸 Screenshot → {path_png}")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 2000) : '(vide)'")
        with open(f"screenshots/timeout_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"  ⚠ Capture debug échouée : {e}")


async def _read_field(page, selector: str, extract: str = "value") -> str:
    loc = page.locator(selector)
    if await loc.count() == 0:
        return ""
    if extract == "value":
        return (await loc.first.input_value() or "").strip()
    return (await loc.first.inner_text() or "").strip()


async def scrape_company(page, company_id: str) -> dict:
    await page.goto(ADMIN_URL.format(id=company_id), wait_until="domcontentloaded", timeout=20000)
    await asyncio.sleep(2)
    await page.wait_for_selector('input[name="company_name"]', timeout=15000)

    return {
        "company_id":    company_id,
        "raison_sociale": await _read_field(page, 'input[name="company_name"]'),
        "company_ref":   await _read_field(page, 'code[data-e2e="companyRef"]', "text"),
        "adresse":       await _read_field(page, 'input[name="personal_address"]'),
        "code_postal":   await _read_field(page, 'input[name="personal_post_code"]'),
        "ville":         await _read_field(page, 'input[name="personal_city"]'),
        "siret":         await _read_field(page, 'input[name="siret"]'),
        "tva":           await _read_field(page, 'input[name="vat_number"]'),
        "nom_commercial": await _read_field(page, 'input[name="user_owner[brand_name]"]'),
        "email":         await _read_field(page, 'input[name="contact_email"]'),
    }


def _build_legal_name(raison_sociale: str, nom_commercial: str) -> str:
    if not nom_commercial or nom_commercial.strip().lower() == raison_sociale.strip().lower():
        return raison_sociale
    return f"{raison_sociale} ({nom_commercial})"


def validate_scraped(scraped: dict) -> list[str]:
    """Retourne la liste des champs requis vides après scraping (SIRET, TVA, email)."""
    return [f for f in REQUIRED_SCRAPED_FIELDS if not scraped.get(f)]


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


def _create_customer_api(base_url: str, scraped: dict, headers: dict, dry_run: bool = False) -> dict:
    payload = {
        "type":      "ORGANIZATION",
        "reference": scraped["company_ref"],
        "status":    "ACTIVE",
        "timezone":  "Europe/Paris",
        "email":     scraped["email"],
        "organization": {
            "legal_name":          _build_legal_name(scraped["raison_sociale"], scraped["nom_commercial"]),
            "tax_id":              scraped["tva"],
            "registration_number": scraped["siret"],
            "registered_address": {
                "line1":       scraped["adresse"],
                "city":        scraped["ville"],
                "postal_code": scraped["code_postal"],
                "country":     "FR",
            },
        },
    }
    # Clé stable (dérivée du company_ref) : un retry après timeout réutilise la même
    # clé au lieu d'en générer une nouvelle, ce qui évite de créer un customer en double.
    idempotency_key = f"create-customer-{scraped['company_ref']}"

    if dry_run:
        print(f"  ⏭ SIMULÉ (DRY_RUN) — payload : {json.dumps(payload, ensure_ascii=False)}")
        return {"id": "", "dry_run": True}

    r = _request_with_retry(
        "POST",
        f"{base_url}/customers",
        json=payload,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    r.raise_for_status()
    return r.json()


async def main():
    os.makedirs("results", exist_ok=True)

    env, dry_run = ask_config()
    base_url = ENVS[env]
    api_key  = API_KEYS.get(env, "")
    if not api_key:
        print(f"⚠ Clé API Solvimon manquante pour l'environnement '{env}' — renseigne SOLVIMON_API_KEY_{env.upper()} dans .env.")
        sys.exit(1)

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY":    api_key,
    }

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id" not in df.columns:
        print("⚠ Colonne 'id' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["id"])

    if TEST_MODE:
        df = df.head(1)
        print("[MODE TEST] 1 seul ID traité.\n")
    else:
        print(f"[PROD] {len(df)} ID(s) à traiter.\n")

    results = []

    log_f  = open(LOG_FILE, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(log_f, fieldnames=[
        "company_id", "company_ref", "raison_sociale", "nom_commercial",
        "siret", "email", "solvimon_id", "status", "message",
    ])
    writer.writeheader()
    log_f.flush()

    def log_result(r: dict) -> None:
        results.append(r)
        writer.writerow(r)
        log_f.flush()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session(ADMIN_SESSION))
        page    = await context.new_page()

        for _, row in df.iterrows():
            company_id = str(row["id"]).strip()
            print(f"\n→ [{company_id}]")

            result = {
                "company_id":    company_id,
                "company_ref":   "",
                "raison_sociale": "",
                "nom_commercial": "",
                "siret":         "",
                "email":         "",
                "solvimon_id":   "",
                "status":        "",
                "message":       "",
            }

            try:
                scraped = await scrape_company(page, company_id)
                result.update({
                    "company_ref":   scraped["company_ref"],
                    "raison_sociale": scraped["raison_sociale"],
                    "nom_commercial": scraped["nom_commercial"],
                    "siret":         scraped["siret"],
                    "email":         scraped["email"],
                })
                print(f"  ✓ Scraping — {scraped['raison_sociale']} ({scraped['company_ref']})")

                if not scraped["company_ref"]:
                    result["status"]  = "ERREUR_SCRAPING"
                    result["message"] = "company_ref vide après scraping"
                    print("  ✗ company_ref introuvable")
                    log_result(result)
                    continue

                missing = validate_scraped(scraped)
                if missing:
                    result["status"]  = "ERREUR_VALIDATION"
                    result["message"] = f"champ(s) requis manquant(s) : {', '.join(missing)}"
                    print(f"  ✗ Champ(s) manquant(s) après scraping : {', '.join(missing)} — envoi Solvimon annulé")
                    log_result(result)
                    continue

                created = _create_customer_api(base_url, scraped, headers, dry_run)
                result["solvimon_id"] = created.get("id", "")
                result["status"]      = "OK"
                if dry_run:
                    result["message"] = "simulé (dry_run)"
                    print(f"  ⏭ [DRY_RUN] Customer non créé (simulation)")
                else:
                    print(f"  ✓ Customer Solvimon créé → {result['solvimon_id']}")

            except PlaywrightTimeout as e:
                result["status"]  = "ERREUR_TIMEOUT"
                result["message"] = str(e)[:120]
                print(f"  ✗ Timeout scraping : {str(e)[:80]}")
                await _screenshot_timeout(page, company_id)

            except requests.HTTPError as e:
                result["status"]  = f"ERREUR_HTTP_{e.response.status_code}"
                result["message"] = e.response.text[:200]
                print(f"  ✗ Solvimon HTTP {e.response.status_code} : {e.response.text[:100]}")

            except Exception as e:
                result["status"]  = "ERREUR"
                result["message"] = str(e)[:120]
                print(f"  ✗ {str(e)[:80]}")

            log_result(result)
            await asyncio.sleep(DELAY_BETWEEN)

        await context.close()
        await browser.close()

    log_f.close()

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
