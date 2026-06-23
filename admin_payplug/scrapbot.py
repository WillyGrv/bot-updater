import asyncio
import csv
import json
import os
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/data.csv"
TEST_MODE = True

SCRAP_TARGETS = {
    "company_ref": {
        "label":      "Company Ref",
        "selector":   'code[data-e2e="companyRef"]',
        "column":     "company_ref",
        "log_prefix": "company_refs",
        "extract":    "text",
    },
    "raison_sociale": {
        "label":      "Raison Sociale",
        "selector":   'input[name="company_name"]',
        "column":     "raison_sociale",
        "log_prefix": "raison_sociale",
        "extract":    "value",
    },
    "siret": {
        "label":      "SIRET",
        "selector":   'input[name="siret"]',
        "column":     "siret",
        "log_prefix": "siret",
        "extract":    "value",
    },
    "nom_commercial": {
        "label":      "Nom Commercial",
        "selector":   'input[name="user_owner[brand_name]"]',
        "column":     "nom_commercial",
        "log_prefix": "nom_commercial",
        "extract":    "value",
    },
    # Ajouter ici de nouveaux types de données à scraper
}
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


def ask_config() -> str:
    """Retourne la clé du target choisi. Dashboard : JSON {"target": "..."}. Terminal : menu interactif."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("DONNÉES À SCRAPER")
        print("─────────────────────────────────────────────")
        for i, (key, tdef) in enumerate(SCRAP_TARGETS.items(), 1):
            print(f"  [{i}] {tdef['label']}")
        choice = input("  Votre choix (défaut: 1) : ").strip()
        keys = list(SCRAP_TARGETS.keys())
        idx  = (int(choice) - 1) if choice.isdigit() and 0 < int(choice) <= len(keys) else 0
        target = keys[idx]
        print(f"  → {SCRAP_TARGETS[target]['label']}")
        print("─────────────────────────────────────────────\n")
        return target
    try:
        raw    = input().strip()
        target = raw if raw in SCRAP_TARGETS else list(SCRAP_TARGETS.keys())[0]
        print(f"  ✓ Cible : {SCRAP_TARGETS[target]['label']}\n")
        return target
    except EOFError:
        return list(SCRAP_TARGETS.keys())[0]


async def scrape_row(page, url: str, row_id: str, target: dict) -> dict:
    column = target["column"]
    result = {"id": row_id, "url": url, column: "", "status": "", "message": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        locator = page.locator(target["selector"])
        await locator.wait_for(state="visible", timeout=8000)
        if target.get("extract") == "value":
            value = (await locator.input_value()).strip()
        else:
            value = (await locator.inner_text()).strip()
        result[column] = value
        result["status"] = "OK"
        print(f"  ✓ [{row_id}] {column} → {value}")
    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {e}")
        await _screenshot_timeout(page, row_id)
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")
    return result


async def main():
    os.makedirs("results", exist_ok=True)

    target_key = ask_config()
    if target_key not in SCRAP_TARGETS:
        print(f"⚠ Cible inconnue : {target_key} — arrêt.")
        return
    target = SCRAP_TARGETS[target_key]

    log_file = f"results/{target['log_prefix']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

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
            result = await scrape_row(page, url=row["url"], row_id=row["id"], target=target)
            results.append(result)
            await asyncio.sleep(1)

        await context.close()
        await browser.close()

    fieldnames = ["id", "url", target["column"], "status", "message"]
    with open(log_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
