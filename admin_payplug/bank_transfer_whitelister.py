import asyncio
import csv
import json
import os
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/bank_transfer_ids.csv"
BASE_URL    = "https://admin.payplug.com/admin/bank/transfers/{id}"
LOG_FILE    = f"results/results_whitelist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = True

SEL_EDIT_BTN   = "#edit-company-toggle-button"
SEL_RADIO_WL   = 'input[name="frozen_or_whitelisted_status"][value="whitelisted"]'
SEL_DATE_INPUT = 'input[name="whitelisted_until"]'
SEL_SUBMIT     = "#edit-company-submit"
# ──────────────────────────────────────────────────────────────────────────────


def ask_config() -> str:
    """Retourne la date whitelist (AAAA-MM-JJ).
    - Terminal (TTY) : prompt interactif
    - Dashboard/pipe : 1 ligne
    """
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        date = input("  Date Whitelist (AAAA-MM-JJ) : ").strip()
        print("─────────────────────────────────────────────\n")
        return date
    try:
        date = input().strip()
        print(f"  ✓ Date : {date}\n")
        return date
    except EOFError:
        return ""


def load_session(path: str) -> dict:
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
        await page.screenshot(path=path_png, full_page=True)
        print(f"  📸 Screenshot → {path_png}")
        print(f"  🌐 URL : {page.url}")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 2000) : '(vide)'")
        with open(f"screenshots/timeout_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  📄 HTML → screenshots/timeout_{identifier}.html")
    except Exception as dbg_err:
        print(f"  ⚠ Capture debug échouée : {dbg_err}")


async def whitelist_transfer(page, row_id: str, date: str) -> dict:
    url    = BASE_URL.format(id=row_id)
    result = {"id": row_id, "url": url, "date": date, "status": "", "message": ""}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        # Cliquer sur "éditer"
        await page.wait_for_selector(SEL_EDIT_BTN, timeout=8000)
        await page.click(SEL_EDIT_BTN)

        # Sélectionner le radio "whitelisted"
        await page.wait_for_selector(SEL_RADIO_WL, timeout=8000)
        await page.click(SEL_RADIO_WL)

        # Remplir la date — Tab pour fermer le datepicker et valider la valeur
        await page.wait_for_selector(SEL_DATE_INPUT, timeout=8000)
        await page.fill(SEL_DATE_INPUT, "")
        await page.fill(SEL_DATE_INPUT, date)
        await page.press(SEL_DATE_INPUT, "Tab")
        await asyncio.sleep(0.3)

        # Soumettre
        await page.click(SEL_SUBMIT)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        result["status"] = "OK"
        print(f"  ✓ [{row_id}] Whitelist jusqu'au {date}")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, row_id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    date = ask_config()
    if not date:
        print("⚠ Date non renseignée — arrêt.")
        return

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id" not in df.columns:
        print("⚠ Colonne 'id' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["id"])

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            row_id = str(row["id"]).strip()
            print(f"\n→ Traitement {row_id}")
            result = await whitelist_transfer(page, row_id, date)
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "date", "status", "message"])
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
