import asyncio
import csv
import json
import os
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/channel_accounts.csv"
BASE_URL    = "https://admin.payplug.com/companies/{company_ref}/features"
LOG_FILE    = f"results/results_features_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

SEL_SUBMIT  = 'button[data-e2e="merchant-features-btn-submit"]'
# ──────────────────────────────────────────────────────────────────────────────


def ask_features() -> list:
    """Lit une liste JSON de feature values depuis stdin."""
    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    try:
        selected = json.loads(raw)
        if isinstance(selected, list) and selected:
            return selected
    except Exception:
        pass
    return []


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


async def process_company(page, company_ref: str, features: list) -> dict:
    result = {
        "company_ref": company_ref,
        "features":    ",".join(features),
        "cochees":     "",
        "ignorees":    "",
        "status":      "",
        "message":     "",
    }
    url = BASE_URL.format(company_ref=company_ref)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        cochees  = []
        ignorees = []

        for feat in features:
            sel_input = f'input.ant-checkbox-input[value="{feat}"]'
            loc_input = page.locator(sel_input)

            if await loc_input.count() == 0:
                print(f"  ⚠ [{company_ref}] Checkbox '{feat}' introuvable")
                ignorees.append(feat)
                continue

            # Ignorer si désactivée
            if await loc_input.is_disabled():
                print(f"  ⚠ [{company_ref}] '{feat}' désactivée — ignorée")
                ignorees.append(feat)
                continue

            # Déjà cochée → rien à faire
            if await loc_input.is_checked():
                print(f"  ℹ [{company_ref}] '{feat}' déjà cochée")
                cochees.append(feat)
                continue

            # Cliquer sur le label parent (plus fiable pour Ant Design / React)
            sel_label = f'label:has(input[value="{feat}"])'
            await page.locator(sel_label).click()
            await asyncio.sleep(0.4)
            print(f"  ✓ [{company_ref}] '{feat}' cochée")
            cochees.append(feat)

        # Soumettre
        submit_loc = page.locator(SEL_SUBMIT)
        if await submit_loc.count() == 0:
            raise Exception("Bouton 'Mettre à jour' introuvable")

        await submit_loc.click()
        await asyncio.sleep(2)

        result["cochees"]  = ",".join(cochees)
        result["ignorees"] = ",".join(ignorees)
        result["status"]   = "OK"
        print(f"  ✓ [{company_ref}] Soumis — {len(cochees)} cochée(s), {len(ignorees)} ignorée(s)")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_ref}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, company_ref)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_ref}] {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    features = ask_features()
    if not features:
        print("⚠ Aucune fonctionnalité sélectionnée — arrêt.")
        return

    print(f"  ✓ Features : {', '.join(features)}\n")

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "company_ref" not in df.columns:
        print("⚠ Colonne 'company_ref' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["company_ref"])

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} comptes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            company_ref = str(row["company_ref"]).strip()
            print(f"\n→ Traitement company_ref {company_ref}")
            result = await process_company(page, company_ref, features)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_ref", "features", "cochees", "ignorees", "status", "message"])
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = sum(1 for r in results if r["status"] != "OK")
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
