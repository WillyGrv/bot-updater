import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/bank_transfer_ids.csv"
BASE_URL    = "https://admin.payplug.com/admin/history?name={id}"
LOG_FILE    = f"results/results_payment_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

SEL_DETAIL_LINK = 'table.black-head-tbl tbody tr a[onclick*="openPopDiv"]'
SEL_NUM_TRANSAC = "#num_transac"
# Ajouter ici de nouveaux champs à extraire depuis la popup de détail
# ──────────────────────────────────────────────────────────────────────────────


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


async def process_transaction(page, row_id: str) -> dict:
    url    = BASE_URL.format(id=row_id)
    result = {"id": row_id, "url": url, "num_transaction": "", "status": "", "message": ""}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        detail_link = page.locator(SEL_DETAIL_LINK).first
        if await detail_link.count() == 0:
            result["status"] = "INTROUVABLE"
            print(f"  ⚠ [{row_id}] Aucune transaction trouvée")
            return result

        await detail_link.click()
        await page.wait_for_function(
            "document.querySelector('#num_transac') && document.querySelector('#num_transac').textContent.trim().length > 0",
            timeout=8000,
        )

        num_transaction = (await page.locator(SEL_NUM_TRANSAC).first.inner_text()).strip()
        result["num_transaction"] = num_transaction
        result["status"] = "OK"
        print(f"  ✓ [{row_id}] N° transaction : {num_transaction}")

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
        print(f"[PROD] {len(df)} IDs à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            row_id = str(row["id"]).strip()
            print(f"\n→ Traitement {row_id}")
            result = await process_transaction(page, row_id)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "num_transaction", "status", "message"])
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
