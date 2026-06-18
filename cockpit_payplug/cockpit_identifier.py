import asyncio
import csv
import json
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE  = "input/data.csv"
BASE_URL     = "https://internal-payment.gcp.dlns.io/intranet/admin/udvs/password?idUdv={idUdv}"
EMAIL_FIELD  = "input[name='passwordRecovery[email]']"
SUBMIT_BTN   = "input[type='submit'][name='submit']"
LOG_FILE     = f"results/results_cockpit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False
# ──────────────────────────────────────────────────────────────────────────────


def ask_email() -> str:
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        email = input("  Email à saisir pour chaque UDV : ").strip()
        print("─────────────────────────────────────────────\n")
        return email
    try:
        email = input().strip()
        print(f"  ✓ Email : {email}\n")
        return email
    except EOFError as e:
        print(f"⚠ Aucun email reçu ({e})\n")
        return ""


async def _screenshot_timeout(page, identifier: str) -> None:
    import os
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


async def process_udv(page, id: str, email: str) -> dict:
    result = {"id": id, "email": email, "status": "", "message": ""}
    url    = BASE_URL.format(idUdv=id)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(EMAIL_FIELD, timeout=8000)

        await page.fill(EMAIL_FIELD, "")
        await page.fill(EMAIL_FIELD, email)
        print(f"  ✓ [{id}] Email saisi : {email}")

        await page.click(SUBMIT_BTN)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        result["status"] = "OK"
        print(f"  ✓ [{id}] Submit envoyé")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{id}] Timeout : {e}")
        await _screenshot_timeout(page, id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{id}] {e}")

    return result


async def main():
    email = ask_email()
    if not email:
        print("Aucun email fourni — arrêt.")
        return

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
        print(f"[PROD] {len(df)} UDVs à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            id = str(row["id"]).strip()
            print(f"\n→ Traitement UDV {id}")
            result = await process_udv(page, id, email)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "email", "status", "message"])
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
