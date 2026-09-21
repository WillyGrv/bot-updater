import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE      = "input/data.csv"
LOG_FILE         = f"results/results_check_financial_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
BASE_COCKPIT_URL = "https://internal-payment.gcp.dlns.io/cockpit/"
BASE_URL         = "https://internal-payment.gcp.dlns.io/cockpit/#/intranet/admin/udvs/edit?id={id}"
TEST_MODE = False

SEL_ACCORDION_TOGGLE = 'div[href="#financial-configuration-accordion"]'
SEL_ACCORDION_PANEL  = '#financial-configuration-accordion'
SEL_FINANCIAL_SELECT = 'select#financialAccount'
SEL_CBS_CHECKBOX     = 'input[name="cbsInformation[cbsActivated]"][type="checkbox"]'
# ──────────────────────────────────────────────────────────────────────────────


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


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


async def _navigate_to_udv(page, udv_id: str) -> None:
    """Navigue vers la page d'un UDV (reset SPA + iframe)."""
    url = BASE_URL.format(id=udv_id)
    await page.goto(BASE_COCKPIT_URL, wait_until="domcontentloaded", timeout=10000)
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(4)


async def _find_frame(page, selector: str):
    """Cherche le frame contenant le sélecteur (iframe possible)."""
    for f in page.frames:
        try:
            if await f.locator(selector).count() > 0:
                return f
        except Exception:
            pass
    return None


async def _open_panel_if_needed(frame, toggle_selector: str, panel_selector: str) -> None:
    """Ouvre le panel accordion Bootstrap s'il n'est pas déjà déplié (classe 'in')."""
    panel = frame.locator(panel_selector)
    classes = ""
    if await panel.count() > 0:
        classes = await panel.get_attribute("class") or ""
    if "in" not in classes.split():
        await frame.locator(toggle_selector).click()
        await asyncio.sleep(1)


async def check_udv(page, udv_id: str) -> dict:
    result = {"id": udv_id, "financial_account": "", "cbs_activated": "", "status": "", "message": ""}

    try:
        await _navigate_to_udv(page, udv_id)

        frame = await _find_frame(page, SEL_ACCORDION_TOGGLE)
        if frame is None:
            await _screenshot_failure(page, udv_id, "panel_not_found")
            result.update(status="KO", message="section 'Financial configuration' introuvable")
            return result

        await _open_panel_if_needed(frame, SEL_ACCORDION_TOGGLE, SEL_ACCORDION_PANEL)

        select = frame.locator(SEL_FINANCIAL_SELECT)
        try:
            await select.wait_for(state="attached", timeout=8000)
        except PlaywrightTimeout:
            await _screenshot_failure(page, udv_id, "select_not_found")
            result.update(status="KO", message="liste 'Financial Account' introuvable après ouverture du panel")
            return result

        financial_account = (await select.input_value()).strip()
        result["financial_account"] = financial_account

        checkbox = frame.locator(SEL_CBS_CHECKBOX)
        if await checkbox.count() > 0:
            cbs_checked = await checkbox.is_checked()
            result["cbs_activated"] = "yes" if cbs_checked else "no"
        else:
            result["cbs_activated"] = "introuvable"

        result["status"] = "OK"

        if financial_account:
            print(f"  ✓ [{udv_id}] Financial Account : {financial_account} | Migrated (CBS) : {result['cbs_activated']}")
        else:
            print(f"  ℹ [{udv_id}] Aucun Financial Account sélectionné | Migrated (CBS) : {result['cbs_activated']}")

    except PlaywrightTimeout as e:
        result.update(status="ERREUR_TIMEOUT", message=str(e)[:150])
        print(f"  ✗ [{udv_id}] Timeout : {str(e)[:100]}")
        await _screenshot_failure(page, udv_id, "timeout")

    except Exception as e:
        result.update(status="ERREUR", message=str(e)[:150])
        print(f"  ✗ [{udv_id}] {str(e)[:100]}")
        await _screenshot_failure(page, udv_id, "exception")

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
        print("[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} UDV(s) à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            udv_id = str(row["id"]).strip()
            print(f"\n→ Traitement UDV {udv_id}")
            result = await check_udv(page, udv_id)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "financial_account", "cbs_activated", "status", "message"])
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
