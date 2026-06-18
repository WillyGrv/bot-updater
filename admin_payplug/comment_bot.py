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
LOG_FILE    = f"results/results_comments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

SEL_TEXTAREA  = "#new-comment"
SEL_SUBMIT    = "form#add-comment input[type='submit']"
SEL_LOG_LIST  = "#all-logs"
SEL_FIRST_PIN = "#all-logs .company-log:first-child i.pin"
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


def ask_config() -> tuple[str, bool]:
    """Retourne (comment_text, pin). Dashboard : 2 lignes brutes. Terminal : prompts."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        comment_text = input("  Texte du commentaire : ").strip()
        pin_input    = input("  Épingler le commentaire ? [o/N] : ").strip().lower()
        pin          = pin_input == "o"
        print(f"\n  → Commentaire : {comment_text[:60]}")
        print(f"  → Épinglé     : {'Oui' if pin else 'Non'}")
        print("─────────────────────────────────────────────\n")
        return comment_text, pin
    try:
        comment_text = input().strip()
        pin_raw      = input().strip().lower()
        pin          = pin_raw in ("oui", "o", "true", "1")
        print(f"  ✓ Commentaire : {comment_text[:60]}")
        print(f"  ✓ Épinglé     : {'Oui' if pin else 'Non'}\n")
        return comment_text, pin
    except EOFError:
        return "", False


async def add_comment(page, url: str, row_id: str, comment_text: str, pin: bool) -> dict:
    result = {"id": row_id, "url": url, "pinned": pin, "status": "", "message": ""}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(SEL_TEXTAREA, timeout=8000)

        await page.fill(SEL_TEXTAREA, comment_text)
        await page.click(SEL_SUBMIT)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        print(f"  ✓ [{row_id}] Commentaire ajouté")

        if pin:
            await page.wait_for_selector(SEL_LOG_LIST, timeout=8000)
            first_pin = page.locator(SEL_FIRST_PIN)
            await first_pin.wait_for(state="visible", timeout=8000)
            await first_pin.click()
            await asyncio.sleep(0.5)
            print(f"  ✓ [{row_id}] Commentaire épinglé")

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

    comment_text, pin = ask_config()
    if not comment_text:
        print("Commentaire vide — arrêt.")
        return

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
            print(f"\n→ [{row['id']}] {row['url']}")
            result = await add_comment(
                page,
                url=row["url"],
                row_id=row["id"],
                comment_text=comment_text,
                pin=pin,
            )
            results.append(result)
            await asyncio.sleep(1.2)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "pinned", "status", "message"])
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
