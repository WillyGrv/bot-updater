import asyncio
import csv
import json
import os
import sys
import uuid
import requests
import pandas as pd
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime
from dotenv import load_dotenv

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

API_KEY = os.getenv("SOLVIMON_API_KEY", "YOUR_API_KEY_HERE")
# ──────────────────────────────────────────────────────────────────────────────


def ask_config() -> str:
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        env = input("  Environnement [prod/sandbox] : ").strip().lower()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            env = input().strip().lower()
        except EOFError:
            print("⚠ Paramètre manquant.")
            sys.exit(1)

    if env not in ENVS:
        print(f"⚠ Environnement inconnu : '{env}' — utilise 'prod' ou 'sandbox'.")
        sys.exit(1)

    print(f"  Environnement : {env.upper()} → {ENVS[env]}\n")
    return env


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


def _create_customer_api(base_url: str, scraped: dict, headers: dict) -> dict:
    payload = {
        "type":      "ORGANIZATION",
        "reference": scraped["company_ref"],
        "status":    "ACTIVE",
        "timezone":  "Europe/Paris",
        "email":     scraped["email"],
        "organization": {
            "legal_name": _build_legal_name(scraped["raison_sociale"], scraped["nom_commercial"]),
            "tax_id":     scraped["tva"],
            "registered_address": {
                "line1":       scraped["adresse"],
                "city":        scraped["ville"],
                "postal_code": scraped["code_postal"],
                "country":     "FR",
            },
        },
    }
    r = requests.post(
        f"{base_url}/customers",
        json=payload,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    r.raise_for_status()
    return r.json()


async def main():
    os.makedirs("results", exist_ok=True)

    env = ask_config()
    base_url = ENVS[env]

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY":    API_KEY,
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
        "email", "solvimon_id", "status", "message",
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
                    "email":         scraped["email"],
                })
                print(f"  ✓ Scraping — {scraped['raison_sociale']} ({scraped['company_ref']})")

                if not scraped["company_ref"]:
                    result["status"]  = "ERREUR_SCRAPING"
                    result["message"] = "company_ref vide après scraping"
                    print("  ✗ company_ref introuvable")
                    log_result(result)
                    continue

                created = _create_customer_api(base_url, scraped, headers)
                result["solvimon_id"] = created.get("id", "")
                result["status"]      = "OK"
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
