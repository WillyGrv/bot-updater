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
BASE_URL    = "https://internal-payment.gcp.dlns.io/intranet/admin/clients/edit?id={id}"
TEST_MODE = False

SEL_ACCORDION_TOGGLE = 'div[href="#billing-agreement-accordion"]'
SEL_FEE_ROWS         = 'tr[id^="billing-agreement-fee-"]'
SEL_CHECKBOX         = 'input.billing-agreement-active-checkbox'
SEL_AMOUNT_INPUT     = 'input[name$="[amount]"]'
SEL_SUBMIT           = 'input[type="submit"][name="save"]'

VALID_ACTIONS = {"check", "uncheck_all", "check_all"}
# ──────────────────────────────────────────────────────────────────────────────


def ask_action() -> str:
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        print("  Action :")
        print("    [check]        → Vérifier les fees — lecture seule")
        print("    [uncheck_all]  → Décocher tous les fees actifs")
        print("    [check_all]    → Cocher tous les fees inactifs")
        val = input("  Votre choix : ").strip()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            val = input().strip()
        except EOFError:
            val = ""

    if val not in VALID_ACTIONS:
        print(f"⚠ Valeur invalide '{val}' — doit être check, uncheck_all ou check_all.")
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


async def process_client_fees(page, client_id: str, action: str) -> dict:
    result = {
        "id": client_id, "action": action, "matched_count": 0, "fee_rows": [],
        "skipped_count": 0, "skipped_fees": "", "status": "", "message": "",
    }
    url = BASE_URL.format(id=client_id)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        toggle = page.locator(SEL_ACCORDION_TOGGLE)
        if await toggle.count() > 0:
            await toggle.click()
            await asyncio.sleep(0.5)

        rows = page.locator(SEL_FEE_ROWS)
        row_count = await rows.count()

        fee_rows      = []
        skipped_names = []
        changed       = False

        for i in range(row_count):
            row      = rows.nth(i)
            checkbox = row.locator(SEL_CHECKBOX)
            if await checkbox.count() == 0:
                continue

            is_active = await checkbox.is_checked()
            name      = (await row.locator("td").nth(4).inner_text()).strip()
            amount    = (await row.locator(SEL_AMOUNT_INPUT).input_value()).strip()

            if action == "check":
                if is_active:
                    fee_rows.append({"name": name, "amount": amount})
                else:
                    skipped_names.append(name)

            elif action == "uncheck_all":
                if is_active:
                    await checkbox.set_checked(False)
                    fee_rows.append({"name": name, "amount": amount})
                    changed = True
                else:
                    skipped_names.append(name)

            elif action == "check_all":
                if not is_active:
                    await checkbox.set_checked(True)
                    fee_rows.append({"name": name, "amount": amount})
                    changed = True
                else:
                    skipped_names.append(name)

        if changed:
            submit = page.locator(SEL_SUBMIT).first
            if await submit.count() > 0:
                await submit.click()
                await asyncio.sleep(2)
                print(f"  ✓ [{client_id}] Formulaire sauvegardé")
            else:
                print(f"  ⚠ [{client_id}] Bouton Save introuvable — modifications non sauvegardées")

        result["matched_count"] = len(fee_rows)
        result["fee_rows"]      = fee_rows
        result["skipped_count"] = len(skipped_names)
        result["skipped_fees"]  = " | ".join(skipped_names)
        result["status"]        = "OK"

        if action == "check":
            if fee_rows:
                print(f"  ✓ [{client_id}] {len(fee_rows)} fee(s) actif(s) :")
                for fee in fee_rows:
                    print(f"      - {fee['name']} : {fee['amount']}€")
            else:
                print(f"  ℹ [{client_id}] Aucun fee actif")
        else:
            verb = "décoché(s)" if action == "uncheck_all" else "coché(s)"
            if fee_rows:
                print(f"  ✓ [{client_id}] {len(fee_rows)} fee(s) {verb} :")
                for fee in fee_rows:
                    print(f"      - {fee['name']} : {fee['amount']}€")
            else:
                print(f"  ℹ [{client_id}] Aucun fee à {'décocher' if action == 'uncheck_all' else 'cocher'}")

        if skipped_names:
            print(f"  ⋯ [{client_id}] {len(skipped_names)} fee(s) ignoré(s)")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{client_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, client_id)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{client_id}] {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    action = ask_action()
    if not action:
        print("Valeur invalide — arrêt.")
        return

    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = f"results/results_additional_fees_client_{ts}.csv"

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
        print(f"[PROD] {len(df)} clients à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            client_id = str(row["id"]).strip()
            print(f"\n→ Traitement client {client_id}")
            result = await process_client_fees(page, client_id, action)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    fieldnames = [
        "id", "action", "fee_name", "amount",
        "matched_count", "skipped_count", "skipped_fees", "status", "message",
    ]
    csv_rows = []
    for r in results:
        base = {
            "id": r["id"], "action": r["action"],
            "matched_count": r["matched_count"], "skipped_count": r["skipped_count"],
            "skipped_fees": r["skipped_fees"], "status": r["status"], "message": r["message"],
        }
        if r["fee_rows"]:
            for fee in r["fee_rows"]:
                csv_rows.append({**base, "fee_name": fee["name"], "amount": fee["amount"]})
        else:
            csv_rows.append({**base, "fee_name": "", "amount": ""})

    with open(log_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok

    print(f"\n─────────────────────────────")
    print(f"✓ Traités : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"\nExtract :")
    for r in results:
        if r["status"] != "OK":
            continue
        if r["fee_rows"]:
            for fee in r["fee_rows"]:
                print(f"  [{r['id']}] → {fee['name']} : {fee['amount']}€")
        else:
            print(f"  [{r['id']}] → (aucun)")
    print(f"\nLog sauvegardé → {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
