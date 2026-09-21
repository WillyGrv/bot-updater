"""
ChannelScrapperBot
───────────────────
Pour chaque company_ref de input/channel_accounts.csv, sur
https://admin.payplug.com/companies/{company_ref}/channels :
  1. Repère TOUTES les sections dont l'en-tête contient "DALENYS" (il peut y en avoir
     plusieurs, ex. "DALENYS (MID par défaut)" et "DALENYS") et les ouvre si repliées.
  2. Parmi elles, ne retient que celle dont "MID creator" == EXPECTED_MID_CREATOR —
     les autres ne sont pas la bonne "box" même si le nom de section correspond.
  3. Lit sa ligne "Date de création" et la reporte dans le CSV de résultats.

Lancement (terminal) :
    python channel_scrapper_bot.py
Ou depuis le dashboard (bouton "ChannelScrapperBot" du bot Admin PayPlug).
"""
import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/channel_accounts.csv"
LOG_FILE    = f"results/results_channel_scrapper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

BASE_URL = "https://admin.payplug.com/companies/{company_ref}/channels"

SEL_DALENYS_HEADER = 'div[role="button"]'
SEL_MID_CREATOR    = '[data-e2e="channelAuthor"]'
SEL_DATE_CREATION  = '[data-e2e="added"]'

# Identifie la bonne section DALENYS parmi celles qui matchent par nom.
EXPECTED_MID_CREATOR = "wgravier@payplug.com"
# ──────────────────────────────────────────────────────────────────────────────


async def _screenshot_failure(page, identifier: str, tag: str = "failure") -> None:
    os.makedirs("screenshots", exist_ok=True)
    try:
        path_png = f"screenshots/{tag}_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True)
        print(f"  📸 Screenshot → {path_png}")
        print(f"  🌐 URL : {page.url}")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 2000) : '(vide)'")
        with open(f"screenshots/{tag}_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  📄 HTML → screenshots/{tag}_{identifier}.html")
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


async def _open_panel_if_needed(header) -> "object":
    """Retourne le .MuiCollapse-root associé à l'en-tête, en l'ouvrant s'il est replié."""
    collapse_panel = header.locator("xpath=following-sibling::div[contains(@class,'MuiCollapse-root')][1]")
    classes = ""
    if await collapse_panel.count() > 0:
        classes = await collapse_panel.get_attribute("class") or ""
    if "MuiCollapse-entered" not in classes:
        await header.click()
        await asyncio.sleep(1)
    return collapse_panel


async def scrape_dalenys_date(page, row_id: str, company_ref: str) -> dict:
    url = BASE_URL.format(company_ref=company_ref)
    result = {"id": row_id, "company_ref": company_ref, "date_creation": "", "status": "", "message": ""}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)  # la page channels peut mettre un certain temps à charger

        # Plusieurs sections peuvent avoir "DALENYS" dans leur nom (ex. "DALENYS (MID
        # par défaut)" ET "DALENYS") — on les ouvre toutes et on ne retient que celle
        # dont le MID creator correspond, quel que soit l'ordre où elles apparaissent.
        headers = page.locator(SEL_DALENYS_HEADER).filter(has_text="DALENYS")
        try:
            await headers.first.wait_for(state="visible", timeout=10000)
        except PlaywrightTimeout:
            await _screenshot_failure(page, row_id, "dalenys_not_found")
            result.update(status="KO", message="aucune section 'DALENYS' trouvée (page non chargée après 10s)")
            return result

        candidate_count = await headers.count()
        found_panel = None
        seen_creators = []

        for i in range(candidate_count):
            header = headers.nth(i)
            panel  = await _open_panel_if_needed(header)

            mid_creator_cell = panel.locator(SEL_MID_CREATOR)
            if await mid_creator_cell.count() == 0:
                continue
            mid_creator = (await mid_creator_cell.inner_text()).strip()
            seen_creators.append(mid_creator)

            if mid_creator == EXPECTED_MID_CREATOR:
                found_panel = panel
                break

        if found_panel is None:
            await _screenshot_failure(page, row_id, "wrong_box")
            result.update(
                status="KO",
                message=(
                    f"aucune des {candidate_count} section(s) 'DALENYS' n'a MID creator == "
                    f"'{EXPECTED_MID_CREATOR}' (trouvé : {', '.join(seen_creators) or 'aucun'})"
                ),
            )
            return result

        date_cell = found_panel.locator(SEL_DATE_CREATION)
        try:
            await date_cell.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeout:
            await _screenshot_failure(page, row_id, "date_not_found")
            result.update(status="KO", message="ligne 'Date de création' introuvable dans la bonne section DALENYS")
            return result

        date_creation = (await date_cell.inner_text()).strip()
        result["date_creation"] = date_creation
        result["status"] = "OK"
        print(f"  ✓ [{row_id}] DALENYS (MID creator confirmé) — Date de création : {date_creation}")

    except PlaywrightTimeout as e:
        result.update(status="ERREUR_TIMEOUT", message=str(e)[:150])
        print(f"  ✗ [{row_id}] Timeout : {str(e)[:100]}")
        await _screenshot_failure(page, row_id, "timeout")

    except Exception as e:
        result.update(status="ERREUR", message=str(e)[:150])
        print(f"  ✗ [{row_id}] {str(e)[:100]}")
        await _screenshot_failure(page, row_id, "exception")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id" not in df.columns or "company_ref" not in df.columns:
        print("⚠ Colonnes 'id' et 'company_ref' requises dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["id", "company_ref"])

    if TEST_MODE:
        df = df.head(1)
        print("[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} ligne(s) à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            row_id      = str(row["id"]).strip()
            company_ref = str(row["company_ref"]).strip()
            print(f"\n→ [{row_id}] company_ref={company_ref}")
            result = await scrape_dalenys_date(page, row_id, company_ref)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "company_ref", "date_creation", "status", "message"])
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
