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
LOG_FILE    = f"results/solvimon_virement_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

BASE_URL = "https://admin.payplug.com/admin/companies/{id}"

SOLVIMON_TEXT = "Ce marchand est facturé via Solvimon"

SEL_ACCOUNTING_LINK  = 'a[data-e2e="accounting"]'
SEL_TRANSFERS_LINK   = 'a[data-e2e="queued-transfers"][href^="/bank-transfers-company/"]'
SEL_CONFIG_LINK      = 'a[id="configuration"]'
SEL_VIREMENT_STATUS  = '[data-e2e="business-day-status"]'
# ──────────────────────────────────────────────────────────────────────────────


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
    except Exception as dbg_err:
        print(f"  ⚠ Capture debug échouée : {dbg_err}")


async def check_row(page, row_id: str, url: str) -> dict:
    result = {
        "id": row_id,
        "url": url,
        "solvimon": "",
        "virement_statut": "",
        "status": "",
        "message": "",
    }

    try:
        # ── 1. Page principale ──────────────────────────────────────────────
        await page.goto(url, wait_until="networkidle", timeout=20000)

        # ── 2. Documents comptables ─────────────────────────────────────────
        await page.wait_for_selector(SEL_ACCOUNTING_LINK, timeout=10000)
        await page.click(SEL_ACCOUNTING_LINK)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        solvimon_present = SOLVIMON_TEXT in body_text
        result["solvimon"] = "PRÉSENT" if solvimon_present else "MANQUANT"
        print(f"  {'✓' if solvimon_present else '✗'} [{row_id}] Solvimon → {result['solvimon']}")

        # ── 3. Retour page principale ───────────────────────────────────────
        await page.goto(url, wait_until="networkidle", timeout=20000)
        await asyncio.sleep(1)

        # ── 4. Virements ────────────────────────────────────────────────────
        await page.wait_for_selector(SEL_TRANSFERS_LINK, timeout=10000)
        await page.click(SEL_TRANSFERS_LINK)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        # ── 5. Onglet Configuration ─────────────────────────────────────────
        await page.wait_for_selector(SEL_CONFIG_LINK, timeout=10000)
        await page.click(SEL_CONFIG_LINK)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        # ── 6. Statut virements ─────────────────────────────────────────────
        await page.wait_for_selector(SEL_VIREMENT_STATUS, timeout=10000)
        status_text = (await page.locator(SEL_VIREMENT_STATUS).inner_text()).strip()
        # Extrait "Activé" ou "Désactivé" depuis "Statut: Activé"
        statut = status_text.replace("Statut:", "").strip().split("\n")[-1].strip()
        result["virement_statut"] = statut
        print(f"  ✓ [{row_id}] Statut virement → {statut}")

        result["status"] = "OK"

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
        print("⚠ Colonne 'id' requise dans le CSV — arrêt.")
        return

    # 'url' optionnelle : si absente (ou vide sur une ligne), on la construit depuis l'id.
    if "url" not in df.columns:
        df["url"] = pd.NA
    missing_url = df["url"].isna() | (df["url"].astype(str).str.strip() == "")
    if missing_url.any():
        df.loc[missing_url, "url"] = df.loc[missing_url, "id"].apply(lambda v: BASE_URL.format(id=str(v).strip()))

    df = df.dropna(subset=["id", "url"])

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
            row_id = str(row["id"]).strip()
            url    = str(row["url"]).strip()
            print(f"\n→ Traitement {row_id} | {url}")
            result = await check_row(page, row_id, url)
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    fieldnames = ["id", "url", "solvimon", "virement_statut", "status", "message"]
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
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
