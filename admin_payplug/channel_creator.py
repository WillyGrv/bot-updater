import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE  = "input/channel_accounts.csv"
LOG_FILE     = f"results/results_channel_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

BASE_URL     = "https://admin.payplug.com/companies/{company_ref}/channels"

SEL_MID_TYPE_TRIGGER = "[data-e2e='mid-type'] .MuiSelect-select"
SEL_LISTBOX          = "ul[role='listbox']"
SEL_FIRST_OPTION     = "ul[role='listbox'] li[role='option']:first-child"
SEL_CREATE_BTN       = "button[data-e2e='channel-create']"
SEL_SNACKBAR_ERROR   = ".MuiSnackbarContent-root.error .MuiSnackbarContent-message"
# ───────────────────────────────────────────────────────────────────────────────


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


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def create_channel(page, row_id: str, company_ref: str) -> dict:
    url    = BASE_URL.format(company_ref=company_ref)
    result = {"id": row_id, "company_ref": company_ref, "url": url, "status": "", "message": ""}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        # ── Ouvrir la liste déroulante MID type ───────────────────────────────
        trigger = page.locator(SEL_MID_TYPE_TRIGGER)
        await trigger.wait_for(state="visible", timeout=10000)
        await trigger.click()
        await asyncio.sleep(0.5)

        # ── Sélectionner "MID Payfac Ecom Low risk" ──────────────────────────
        listbox = page.locator(SEL_LISTBOX)
        await listbox.wait_for(state="visible", timeout=8000)

        option = page.locator("ul[role='listbox'] li[role='option']").filter(has_text="Low risk").first
        if await option.count() == 0:
            option = page.locator(SEL_FIRST_OPTION)

        option_text = (await option.text_content() or "").strip()
        await option.click()
        await asyncio.sleep(0.3)
        print(f"  ✓ [{row_id}] Option sélectionnée : {option_text}")

        # ── Cliquer sur Créer ─────────────────────────────────────────────────
        create_btn = page.locator(SEL_CREATE_BTN)
        await create_btn.wait_for(state="visible", timeout=8000)
        await create_btn.click()
        await asyncio.sleep(1.5)

        # ── Vérifier si une erreur snackbar apparaît ──────────────────────────
        snackbar = page.locator(SEL_SNACKBAR_ERROR)
        if await snackbar.is_visible(timeout=2000):
            error_msg = (await snackbar.text_content() or "").strip()
            result["status"]  = "ERREUR_API"
            result["message"] = error_msg
            print(f"  ✗ [{row_id}] Erreur API : {error_msg}")
            return result

        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        print(f"  ✓ [{row_id}] Channel créé")
        result["status"] = "OK"

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:150]
        print(f"  ✗ [{row_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, row_id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:150]
        print(f"  ✗ [{row_id}] {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if TEST_MODE:
        df = df.head(1)
        print("[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            row_id      = str(row["id"]).strip()
            company_ref = str(row["company_ref"]).strip()
            print(f"\n→ [{row_id}] company_ref={company_ref}")
            result = await create_channel(page, row_id, company_ref)
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "company_ref", "url", "status", "message"])
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
