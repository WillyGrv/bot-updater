import asyncio
import csv
import json
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

DATA_SOURCE       = "data.csv"
LOG_FILE          = f"company_refs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE         = False

COMPANY_REF_SELECTOR = 'code[data-e2e="companyRef"]'


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def scrape_company_ref(page, url: str, row_id: str) -> dict:
    result = {"id": row_id, "url": url, "company_ref": "", "status": "", "message": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        locator = page.locator(COMPANY_REF_SELECTOR)
        await locator.wait_for(state="visible", timeout=8000)
        company_ref = (await locator.inner_text()).strip()
        result["company_ref"] = company_ref
        result["status"]      = "OK"
        print(f"  ✓ [{row_id}] company_ref → {company_ref}")
    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {e}")
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")
    return result


async def main():
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
            print(f"\n→ Scraping {row['id']} | {row['url']}")
            result = await scrape_company_ref(page, url=row["url"], row_id=row["id"])
            results.append(result)
            await asyncio.sleep(1)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "company_ref", "status", "message"])
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
