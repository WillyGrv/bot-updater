import asyncio
import csv
import json
import os
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE      = "input/data.csv"
BASE_COCKPIT_URL = "https://internal-payment.gcp.dlns.io/cockpit/"
BASE_URL         = (
    "https://internal-payment.gcp.dlns.io/cockpit/#/intranet/admin/mids/edit"
    "?idMid={id}&idSupplier=9&idClientsGroup=2724&isProximity=yes"
)
TEST_MODE = False

SEL_THRESHOLD = '#smartAccepteurCreditThreshold'
SEL_SIRET     = 'input[name="generalInformation[siret]"]'
SEL_SUBMIT    = "#cockpit-mode > div > form > p > input.btn.btn-primary.mleft"
SEL_SUCCESS   = "div.alert.alert-success"

THRESHOLD_VALUES = {"1", "20", "400", "analyse"}
VALID_ACTIONS    = THRESHOLD_VALUES | {"check_siret"}
# ──────────────────────────────────────────────────────────────────────────────


def ask_action() -> str:
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        print("  Action :")
        print("    [check_siret] → Vérifier SIRET")
        print("    [analyse]     → Seuil Smart — lecture seule")
        print("    [1]           → Appliquer Seuil → 1")
        print("    [20]          → Appliquer Seuil → 20")
        print("    [400]         → Appliquer Seuil → 400")
        val = input("  Votre choix : ").strip()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            val = input().strip()
        except EOFError:
            val = ""

    if val not in VALID_ACTIONS:
        print(f"⚠ Valeur invalide '{val}' — doit être check_siret, analyse, 1, 20 ou 400.")
        return ""

    print(f"  ✓ Action : {val}\n")
    return val


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


async def _navigate_to_mid(page, mid_id: str) -> None:
    """Navigue vers la page d'un MID (reset SPA + iframe)."""
    url = BASE_URL.format(id=mid_id)
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


# ── Mode : vérification SIRET ─────────────────────────────────────────────────
async def process_mid_siret(page, mid_id: str) -> dict:
    result = {"id": mid_id, "siret": "", "status": "", "message": ""}

    try:
        await _navigate_to_mid(page, mid_id)

        frame = await _find_frame(page, SEL_SIRET)
        if frame is None:
            raise Exception("Élément SIRET introuvable dans tous les frames")

        siret = await frame.locator(SEL_SIRET).input_value()
        result["siret"]  = siret
        result["status"] = "OK"
        print(f"  ✓ [{mid_id}] SIRET : {siret}")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{mid_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, mid_id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{mid_id}] {str(e)[:80]}")

    return result


# ── Mode : Seuil Smart Accepteur ──────────────────────────────────────────────
async def process_mid_threshold(page, mid_id: str, threshold: str) -> dict:
    result = {"id": mid_id, "threshold": threshold, "current_value": "", "status": "", "message": ""}

    try:
        await _navigate_to_mid(page, mid_id)

        frame = await _find_frame(page, SEL_THRESHOLD)
        if frame is None:
            raise Exception(f"Élément {SEL_THRESHOLD} introuvable dans tous les frames")

        current = await frame.locator(SEL_THRESHOLD).input_value()
        result["current_value"] = current
        print(f"  ℹ [{mid_id}] Valeur actuelle : {current}")

        if threshold == "analyse":
            result["status"] = "ANALYSÉ"
            return result

        if current == threshold:
            result["status"] = "DÉJÀ OK"
            print(f"  ✓ [{mid_id}] Déjà à {threshold} — aucune modification")
            return result

        await frame.select_option(SEL_THRESHOLD, value=threshold)
        print(f"  ✓ [{mid_id}] Seuil sélectionné : {threshold}")

        await frame.click(SEL_SUBMIT)
        await asyncio.sleep(2)
        result["status"] = "OK"
        print(f"  ✓ [{mid_id}] Formulaire soumis")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{mid_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, mid_id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{mid_id}] {str(e)[:80]}")

    return result


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    os.makedirs("results", exist_ok=True)

    action = ask_action()
    if not action:
        print("Valeur invalide — arrêt.")
        return

    is_siret = (action == "check_siret")
    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = (
        f"results/results_mid_siret_{ts}.csv"
        if is_siret
        else f"results/results_mid_updater_{ts}.csv"
    )

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
        print(f"[PROD] {len(df)} MIDs à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            mid_id = str(row["id"]).strip()
            print(f"\n→ Traitement MID {mid_id}")
            if is_siret:
                result = await process_mid_siret(page, mid_id)
            else:
                result = await process_mid_threshold(page, mid_id, action)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    fieldnames = (
        ["id", "siret", "status", "message"]
        if is_siret
        else ["id", "threshold", "current_value", "status", "message"]
    )

    with open(log_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = sum(1 for r in results if r["status"] not in ("OK", "DÉJÀ OK", "ANALYSÉ"))

    print(f"\n─────────────────────────────")
    if is_siret:
        print(f"ℹ SIRETs vérifiés : {ok}")
        for r in results:
            print(f"  [{r['id']}] → {r['siret'] or '(vide)'}")
    else:
        skipped  = sum(1 for r in results if r["status"] == "DÉJÀ OK")
        analysed = sum(1 for r in results if r["status"] == "ANALYSÉ")
        if analysed:
            print(f"ℹ Analysés  : {analysed}")
            for r in results:
                print(f"  [{r['id']}] → {r['current_value']}")
        else:
            print(f"✓ Modifiés  : {ok}")
            print(f"✓ Déjà OK   : {skipped}")
            print(f"✗ Erreurs   : {err}")
    print(f"Log sauvegardé → {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
