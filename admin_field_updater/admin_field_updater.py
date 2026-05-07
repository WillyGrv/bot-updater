import asyncio
import csv
import json
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "data.csv"

EDIT_BUTTON  = "#change"
VALUE_INPUT  = "input[name='master_company_id']"
SAVE_BUTTON  = "input[type='submit'].main-submit"

LOG_FILE          = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE         = False
DEFAULT_MASTER_VALUE = ""
# ──────────────────────────────────────────────────────────────────────────────


def ask_config() -> str:
    try:
        value = input("Valeur Compte Master à appliquer : ").strip()
        if not value:
            raise ValueError("valeur vide")
        print(f"\n→ Valeur : {value}")
        print("─────────────────────────────────────────────\n")
        return value
    except (EOFError, ValueError):
        print(f"Mode dashboard — valeur par défaut : '{DEFAULT_MASTER_VALUE}'\n")
        return DEFAULT_MASTER_VALUE


def load_session(path: str) -> dict:
    """Charge la session et s'assure que toutes les valeurs localStorage sont des strings."""
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def update_page(page, url: str, master_value: str, row_id: str) -> dict:
    value = master_value
    result = {"id": row_id, "url": url, "master_value": master_value, "status": "", "message": ""}
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
    master_value = ask_config()

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page = await context.new_page()

        for _, row in df.iterrows():
            print(f"→ Traitement {row['id']} | {row['url']}")
            result = await update_page(
                page,
                url=row["url"],
                master_value=master_value,
                row_id=row["id"],
            )
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    # Sauvegarde du log
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "master_value", "status", "message"])
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