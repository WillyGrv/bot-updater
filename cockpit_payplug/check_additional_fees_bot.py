"""
CheckAdditionalFeesBot
───────────────────────
Fusion de Check Additional Fees (Client) et Check Additional Fees (UDV) : pour
chaque ligne de input/check_fees.csv (colonnes ID_Client, ID_UDV), vérifie ou
met à jour les fees de la section Billing Agreement — au niveau client si
ID_Client est renseigné, au niveau UDV si ID_UDV est renseigné. Les deux
vérifications sont indépendantes l'une de l'autre (une ligne peut n'en
renseigner qu'une des deux, ou les deux), mais s'exécutent en un seul
déclenchement du script, dans un seul fichier de résultat.

Le fichier de sortie porte une colonne "level" (client ou udv) qui indique
sans ambiguïté à quel niveau le fee a été relevé.

Un même ID_Client peut se répéter sur plusieurs lignes (un ID_UDV différent
par ligne) : le check client n'est alors exécuté qu'une seule fois (1ère
occurrence), les doublons suivants sont ignorés — seul l'ID_UDV est traité
sur chaque ligne où il apparaît.

Le Trade name (section "General information", accordéon repliée par défaut —
la valeur reste lisible dans le DOM sans avoir besoin de l'ouvrir) est relevé
pour chaque client/UDV et ajouté au fichier de sortie.
"""
import asyncio
import csv
import json
import os
import sys
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE      = "input/check_fees.csv"
BASE_CLIENT_URL  = "https://internal-payment.gcp.dlns.io/intranet/admin/clients/edit?id={id}"
BASE_COCKPIT_URL = "https://internal-payment.gcp.dlns.io/cockpit/"
BASE_UDV_URL     = "https://internal-payment.gcp.dlns.io/cockpit/#/intranet/admin/udvs/edit?id={id}"
TEST_MODE = False

SEL_ACCORDION_TOGGLE = 'div[href="#billing-agreement-accordion"]'
SEL_FEE_TABLE        = '#additional-invoicing'
SEL_FEE_ROWS         = 'tr[id^="billing-agreement-fee-"]'
SEL_CHECKBOX         = 'input.billing-agreement-active-checkbox'
SEL_AMOUNT_INPUT     = 'input[name$="[amount]"]'
# UDV : "Trade name" → generalInformation[tradeName]. Client : pas de champ tradeName
# sur cette page, l'équivalent affiché est generalInformation[name] (raison sociale) —
# on tente les deux, dans cet ordre.
SEL_TRADE_NAME_INPUTS = [
    'input[name="generalInformation[tradeName]"]',
    'input[name="generalInformation[name]"]',
]
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


async def _navigate_to_udv(page, udv_id: str) -> None:
    """Navigue vers la page d'un UDV (reset SPA + iframe)."""
    url = BASE_UDV_URL.format(id=udv_id)
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


async def _get_trade_name(root) -> str:
    """Lit le Trade name dans la section "General information" (accordéon repliée
    par défaut — la valeur reste lisible dans le DOM sans avoir besoin de l'ouvrir).
    `root` : Page (client, pas d'iframe) ou Frame (UDV, via cockpit).
    Essaie tradeName puis, à défaut (page client), name (cf. SEL_TRADE_NAME_INPUTS)."""
    if root is None:
        return ""
    for selector in SEL_TRADE_NAME_INPUTS:
        loc = root.locator(selector)
        if await loc.count() == 0:
            continue
        try:
            value = (await loc.first.input_value()).strip()
        except Exception:
            continue
        if value:
            return value
    return ""


async def _scan_and_apply_fees(rows, action: str) -> tuple[list, list, bool]:
    """Logique commune client/UDV : lit chaque ligne de fee et applique l'action
    demandée. Identique pour les deux niveaux — seul le conteneur `rows` diffère."""
    fee_rows      = []
    skipped_names = []
    changed       = False
    row_count = await rows.count()

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

    return fee_rows, skipped_names, changed


def _print_summary(label: str, id_: str, action: str, fee_rows: list, skipped_names: list) -> None:
    if action == "check":
        if fee_rows:
            print(f"  ✓ [{label} {id_}] {len(fee_rows)} fee(s) actif(s) :")
            for fee in fee_rows:
                print(f"      - {fee['name']} : {fee['amount']}€")
        else:
            print(f"  ℹ [{label} {id_}] Aucun fee actif")
    else:
        verb = "décoché(s)" if action == "uncheck_all" else "coché(s)"
        if fee_rows:
            print(f"  ✓ [{label} {id_}] {len(fee_rows)} fee(s) {verb} :")
            for fee in fee_rows:
                print(f"      - {fee['name']} : {fee['amount']}€")
        else:
            print(f"  ℹ [{label} {id_}] Aucun fee à {'décocher' if action == 'uncheck_all' else 'cocher'}")

    if skipped_names:
        print(f"  ⋯ [{label} {id_}] {len(skipped_names)} fee(s) ignoré(s)")


async def process_client_fees(page, client_id: str, action: str) -> dict:
    result = {
        "level": "client", "id": client_id, "trade_name": "", "action": action, "matched_count": 0,
        "fee_rows": [], "skipped_count": 0, "skipped_fees": "", "status": "", "message": "",
    }
    url = BASE_CLIENT_URL.format(id=client_id)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        result["trade_name"] = await _get_trade_name(page)
        print(f"  ℹ [client {client_id}] Trade name : {result['trade_name'] or '(introuvable)'}")

        toggle = page.locator(SEL_ACCORDION_TOGGLE)
        if await toggle.count() > 0:
            await toggle.click()
            await asyncio.sleep(0.5)

        rows = page.locator(SEL_FEE_ROWS)
        fee_rows, skipped_names, changed = await _scan_and_apply_fees(rows, action)

        if changed:
            submit = page.locator(SEL_SUBMIT).first
            if await submit.count() > 0:
                await submit.click()
                await asyncio.sleep(2)
                print(f"  ✓ [client {client_id}] Formulaire sauvegardé")
            else:
                print(f"  ⚠ [client {client_id}] Bouton Save introuvable — modifications non sauvegardées")

        result["matched_count"] = len(fee_rows)
        result["fee_rows"]      = fee_rows
        result["skipped_count"] = len(skipped_names)
        result["skipped_fees"]  = " | ".join(skipped_names)
        result["status"]        = "OK"
        _print_summary("client", client_id, action, fee_rows, skipped_names)

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [client {client_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, f"client_{client_id}")

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [client {client_id}] {str(e)[:80]}")

    return result


async def process_udv_fees(page, udv_id: str, action: str) -> dict:
    result = {
        "level": "udv", "id": udv_id, "trade_name": "", "action": action, "matched_count": 0,
        "fee_rows": [], "skipped_count": 0, "skipped_fees": "", "status": "", "message": "",
    }

    try:
        await _navigate_to_udv(page, udv_id)

        frame = await _find_frame(page, SEL_ACCORDION_TOGGLE)
        if frame is None:
            raise Exception("Section Billing agreement introuvable dans tous les frames")

        trade_name = await _get_trade_name(frame)
        if not trade_name:
            trade_name = await _get_trade_name(await _find_frame(page, SEL_TRADE_NAME_INPUTS[0]))
        result["trade_name"] = trade_name
        print(f"  ℹ [udv {udv_id}] Trade name : {trade_name or '(introuvable)'}")

        toggle = frame.locator(SEL_ACCORDION_TOGGLE)
        if await toggle.count() > 0:
            await toggle.click()
            await asyncio.sleep(0.5)

        table_frame = await _find_frame(page, SEL_FEE_TABLE)
        if table_frame is None:
            raise Exception(f"Tableau {SEL_FEE_TABLE} introuvable dans tous les frames")

        rows = table_frame.locator(f"{SEL_FEE_TABLE} {SEL_FEE_ROWS}")
        fee_rows, skipped_names, changed = await _scan_and_apply_fees(rows, action)

        if changed:
            submit = table_frame.locator(SEL_SUBMIT).first
            if await submit.count() == 0:
                submit_frame = await _find_frame(page, SEL_SUBMIT)
                submit = submit_frame.locator(SEL_SUBMIT).first if submit_frame else submit
            if await submit.count() > 0:
                await submit.click()
                await asyncio.sleep(2)
                print(f"  ✓ [udv {udv_id}] Formulaire sauvegardé")
            else:
                print(f"  ⚠ [udv {udv_id}] Bouton Save introuvable — modifications non sauvegardées")

        result["matched_count"] = len(fee_rows)
        result["fee_rows"]      = fee_rows
        result["skipped_count"] = len(skipped_names)
        result["skipped_fees"]  = " | ".join(skipped_names)
        result["status"]        = "OK"
        _print_summary("udv", udv_id, action, fee_rows, skipped_names)

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [udv {udv_id}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, f"udv_{udv_id}")

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [udv {udv_id}] {str(e)[:80]}")

    return result


def _clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


async def main():
    os.makedirs("results", exist_ok=True)

    action = ask_action()
    if not action:
        print("Valeur invalide — arrêt.")
        return

    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = f"results/results_additional_fees_{ts}.csv"

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id_client" not in df.columns or "id_udv" not in df.columns:
        print("⚠ Colonnes 'ID_Client' et 'ID_UDV' introuvables dans le CSV — arrêt.")
        return

    # Une ligne peut ne renseigner qu'une des deux colonnes — les deux checks sont
    # indépendants l'un de l'autre, on ne traite que ce qui est réellement rempli.
    # Un même ID_Client peut apparaître sur plusieurs lignes (un ID_UDV différent à
    # chaque fois) : le check client est strictement identique quel que soit l'UDV
    # associé, donc on ne le relance qu'une seule fois (1ère occurrence) — seul
    # l'ID_UDV, lui, est traité sur chaque ligne où il apparaît.
    rows_to_process   = []
    seen_clients      = set()
    duplicate_clients = []
    for _, row in df.iterrows():
        client_id = _clean(row["id_client"])
        udv_id    = _clean(row["id_udv"])

        if client_id:
            if client_id in seen_clients:
                duplicate_clients.append(client_id)
                client_id = ""
            else:
                seen_clients.add(client_id)

        if client_id or udv_id:
            rows_to_process.append((client_id, udv_id))

    if duplicate_clients:
        uniq = ", ".join(sorted(set(duplicate_clients)))
        print(f"ℹ {len(duplicate_clients)} doublon(s) d'ID_Client ignoré(s) (déjà traité(s) sur une ligne précédente) : {uniq}\n")

    if not rows_to_process:
        print("⚠ Aucune ligne avec ID_Client ou ID_UDV renseigné — arrêt.")
        return

    if TEST_MODE:
        rows_to_process = rows_to_process[:1]
        print("[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(rows_to_process)} ligne(s) à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for client_id, udv_id in rows_to_process:
            if client_id:
                print(f"\n→ Traitement client {client_id}")
                results.append(await process_client_fees(page, client_id, action))
                await asyncio.sleep(1.0)
            if udv_id:
                print(f"\n→ Traitement UDV {udv_id}")
                results.append(await process_udv_fees(page, udv_id, action))
                await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    fieldnames = [
        "level", "id", "trade_name", "action", "fee_name", "amount",
        "matched_count", "skipped_count", "skipped_fees", "status", "message",
    ]
    csv_rows = []
    for r in results:
        base = {
            "level": r["level"], "id": r["id"], "trade_name": r["trade_name"], "action": r["action"],
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
        label = f"{r['level']} {r['id']} ({r['trade_name'] or '?'})"
        if r["fee_rows"]:
            for fee in r["fee_rows"]:
                print(f"  [{label}] → {fee['name']} : {fee['amount']}€")
        else:
            print(f"  [{label}] → (aucun)")
    print(f"\nLog sauvegardé → {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
