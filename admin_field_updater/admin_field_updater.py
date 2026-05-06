import asyncio
import csv
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "data.csv"

EDIT_BUTTON  = "#change"
VALUE_INPUT  = "input[name='master_company_id']"
SAVE_BUTTON  = "input[type='submit'].main-submit"

LOG_FILE     = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE    = True   # ← passe à False quand tout est validé
# ──────────────────────────────────────────────────────────────────────────────


async def update_page(page, url: str, value: str, row_id: str) -> dict:
    result = {"id": row_id, "url": url, "value": value, "status": "", "message": ""}
    try:
        # 1. Ouvrir le lien
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        # 2. Cliquer sur Éditer
        await page.wait_for_selector(EDIT_BUTTON, timeout=8000)
        await page.click(EDIT_BUTTON)

        # 3. Remplir la valeur
        await page.wait_for_selector(VALUE_INPUT, timeout=8000)
        await page.fill(VALUE_INPUT, "")
        await page.fill(VALUE_INPUT, str(value))

        # 4. Cliquer sur Enregistrer
        await page.wait_for_selector(SAVE_BUTTON, timeout=5000)
        await page.click(SAVE_BUTTON)

        # 5. Attendre le reload
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        result["status"] = "OK"
        print(f"  ✓ [{row_id}] → {value}")

    except PlaywrightTimeout as e:
        result["status"] = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {e}")

    except Exception as e:
        result["status"] = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")

    return result


async def main():
    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state="session.json")
        page = await context.new_page()

        for _, row in df.iterrows():
            print(f"→ Traitement {row['id']} | {row['url']}")
            result = await update_page(
                page,
                url=row["url"],
                value=row["value"],
                row_id=row["id"],
            )
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    # Sauvegarde du log
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "value", "status", "message"])
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