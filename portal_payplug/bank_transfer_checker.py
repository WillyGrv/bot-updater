import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

DATA_SOURCE  = "input/bank_transfer_accounts.csv"
SESSION_FILE = "session.json"
BANK_URL     = "https://portal.payplug.com/#/bank-transfers"
LOG_FILE     = f"results/results_bank_transfer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE    = False

ACCOUNT_SWITCHER_TRIGGER = "[data-e2e='account-switcher']"
BANNER_SEL               = "[data-e2e='automatic-transfer-banner']"
RADIO_ACTIVE_SEL         = "input[name='status'][value='ACTIVE']"
CONFIRM_BTN_SEL          = "button[data-e2e='transferModal-submit-button']"


async def _screenshot_timeout(page, identifier: str) -> None:
    import os
    os.makedirs("screenshots", exist_ok=True)
    try:
        path_png = f"screenshots/timeout_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True)
        print(f"  📸 Screenshot → {path_png}")
        print(f"  🌐 URL : {page.url}")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 2000) : '(vide)'")
        with open(f"screenshots/timeout_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  📄 HTML → screenshots/timeout_{identifier}.html")
    except Exception as dbg_err:
        print(f"  ⚠ Capture debug échouée : {dbg_err}")


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def switch_account(page, company_ref: str):
    await page.click(ACCOUNT_SWITCHER_TRIGGER)
    await page.wait_for_selector(
        f"[data-e2e='account-switcher-company-{company_ref}']",
        timeout=8000,
    )
    await page.click(f"[data-e2e='account-switcher-company-{company_ref}']")
    await page.wait_for_load_state("domcontentloaded", timeout=15000)


async def check_and_activate(page, company_ref: str, account_name: str) -> dict:
    result = {
        "company_ref":             company_ref,
        "account_name":            account_name,
        "transfer_status_initial": "",
        "status":                  "",
        "message":                 "",
    }

    try:
        # Fermer toute modale résiduelle avant de switcher
        try:
            if await page.locator(CONFIRM_BTN_SEL).is_visible(timeout=1000):
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
        except Exception:
            pass

        await switch_account(page, company_ref)
        print(f"  ✓ Compte actif : {account_name}")

        await page.goto(BANK_URL, wait_until="domcontentloaded", timeout=15000)

        banner = page.locator(BANNER_SEL)
        await banner.wait_for(state="visible", timeout=10000)

        transfer_state = await banner.get_attribute("data-e2e-automatic-transfer")
        result["transfer_status_initial"] = transfer_state or "unknown"

        if transfer_state == "active":
            print(f"  ✓ Virements automatiques : Actif - Quotidien")
            result["status"] = "OK"
            return result

        # ── Inactif → activation ───────────────────────────────────────────────
        print(f"  ℹ Virements automatiques : Inactif → activation...")

        # A. Clic sur le banner pour ouvrir la modale
        await banner.click()

        # B. Sélectionner le radio ACTIVE (via JS pour contourner les overlays React)
        radio = page.locator(RADIO_ACTIVE_SEL)
        await radio.wait_for(state="visible", timeout=8000)
        await page.evaluate(
            "document.querySelector(\"input[name='status'][value='ACTIVE']\").click()"
        )
        await asyncio.sleep(0.3)

        # C. Cliquer sur Confirmer
        confirm_btn = page.locator(CONFIRM_BTN_SEL)
        await confirm_btn.wait_for(state="visible", timeout=8000)
        await confirm_btn.click()
        await asyncio.sleep(2)

        # D. Vérifier que le banner est repassé en "active"
        await banner.wait_for(state="visible", timeout=8000)
        new_state = await banner.get_attribute("data-e2e-automatic-transfer")

        if new_state == "active":
            print(f"  ✓ Activé avec succès → Actif - Quotidien")
            result["status"] = "MODIFIÉ - ACTIF"
        else:
            result["status"]  = "ERREUR_ACTIVATION"
            result["message"] = f"État après confirmation : {new_state}"
            print(f"  ✗ Activation échouée — état : {new_state}")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:150]
        print(f"  ✗ Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, company_ref)
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:150]
        print(f"  ✗ {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

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
            company_ref  = row["company_ref"]
            account_name = row.get("account_name", company_ref)
            print(f"\n→ [{account_name}] {company_ref}")

            result = await check_and_activate(page, company_ref, account_name)
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company_ref", "account_name", "transfer_status_initial", "status", "message",
        ])
        writer.writeheader()
        writer.writerows(results)

    already_ok = sum(1 for r in results if r["status"] == "OK")
    modified   = sum(1 for r in results if r["status"] == "MODIFIÉ - ACTIF")
    err        = sum(1 for r in results if r["status"] not in ("OK", "MODIFIÉ - ACTIF"))
    print(f"\n─────────────────────────────────────")
    print(f"✓ Déjà actifs : {already_ok}")
    print(f"✓ Activés     : {modified}")
    print(f"✗ Erreurs     : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
