import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/realm_users.csv"
BASE_URL    = "https://admin.payplug.com/realm-companies?realm_ref={realm_id}"
LOG_FILE    = f"results/results_realm_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = True

SEL_CHANGE_OWNER_BTN  = 'button[data-e2e="change-owner-button"]'
SEL_INVITE_NEW_USER   = 'button[data-e2e="change-owner-modal-invite-new-user-button"]'
SEL_EMAIL_INPUT       = 'input[data-e2e="invite-user-email-input"]'
SEL_INVITE_SUBMIT_BTN = 'button[data-e2e="invite-user-submit-button"]'
# ──────────────────────────────────────────────────────────────────────────────


def ask_realm_id() -> str:
    """Lit le realm_id depuis stdin."""
    try:
        return input().strip()
    except EOFError:
        return ""


async def _screenshot_timeout(page, identifier: str) -> None:
    os.makedirs("screenshots", exist_ok=True)
    try:
        path_png = f"screenshots/timeout_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True, timeout=15000)
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


async def invite_user(page, realm_id: str, email: str) -> dict:
    result = {"realm_id": realm_id, "email": email, "status": "", "message": ""}

    try:
        invite_new_btn = page.locator(SEL_INVITE_NEW_USER).first
        await invite_new_btn.wait_for(state="visible", timeout=10000)
        await invite_new_btn.click()
        await asyncio.sleep(1)

        email_input = page.locator(SEL_EMAIL_INPUT).first
        await email_input.wait_for(state="visible", timeout=10000)
        await email_input.fill(email)
        await asyncio.sleep(0.3)

        submit_btn = page.locator(SEL_INVITE_SUBMIT_BTN).first
        await submit_btn.click()
        await asyncio.sleep(1.5)

        result["status"] = "OK"
        print(f"  ✓ [{realm_id}] '{email}' invité")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{realm_id}] '{email}' — Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, f"{realm_id}_{email}")

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{realm_id}] '{email}' — {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    realm_id = ask_realm_id()
    if not realm_id:
        print("⚠ Aucun realm_id renseigné — arrêt.")
        return

    print(f"  ✓ Realm ID : {realm_id}\n")

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "email" not in df.columns:
        print("⚠ Colonne 'email' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["email"])

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seul email traité.\n")
    else:
        print(f"[PROD] {len(df)} email(s) à traiter.\n")

    results = []
    url = BASE_URL.format(realm_id=realm_id)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        try:
            print(f"ℹ Ouverture du royaume {realm_id}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            change_owner_btn = page.locator(SEL_CHANGE_OWNER_BTN).first
            await change_owner_btn.wait_for(state="visible", timeout=10000)
            await change_owner_btn.click()
            await asyncio.sleep(1)

            for _, row in df.iterrows():
                email = str(row["email"]).strip()
                print(f"\n→ Invitation de {email}")
                result = await invite_user(page, realm_id, email)
                results.append(result)
                await asyncio.sleep(1.0)

        except PlaywrightTimeout as e:
            print(f"  ✗ [{realm_id}] Erreur d'ouverture — Timeout : {str(e)[:80]}")
            await _screenshot_timeout(page, realm_id)
            for _, row in df.iterrows():
                email = str(row["email"]).strip()
                results.append({
                    "realm_id": realm_id,
                    "email":    email,
                    "status":   "ERREUR_TIMEOUT",
                    "message":  str(e)[:120],
                })

        except Exception as e:
            print(f"  ✗ [{realm_id}] Erreur d'ouverture — {str(e)[:80]}")
            for _, row in df.iterrows():
                email = str(row["email"]).strip()
                results.append({
                    "realm_id": realm_id,
                    "email":    email,
                    "status":   "ERREUR",
                    "message":  str(e)[:120],
                })

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["realm_id", "email", "status", "message"])
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
