import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE  = "input/bank_transfer_accounts.csv"
SESSION_FILE = "session.json"
NOTIF_URL    = "https://portal.payplug.com/#/configuration/notifications"
LOG_FILE     = f"results/results_notifications_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE    = False

ACCOUNT_SWITCHER_TRIGGER = "[data-e2e='account-switcher']"

SEL_SUBMIT_CUSTOMER = "button[data-e2e='notification-panel-customer-submit']"
SEL_SUBMIT_MERCHANT = "button[data-e2e='notification-panel-merchant-submit']"

# Mapping e2e → section (customer / merchant)
CUSTOMER_NOTIFICATIONS = {
    "notifications-customer-email-payment-confirmations": "Confirmations de paiement",
    "notifications-customer-email-refund-confirmations":  "Confirmations de remboursement",
}
MERCHANT_NOTIFICATIONS = {
    "notifications-merchant-email-successful-payments":           "Paiements réussis",
    "notifications-merchant-email-transfers-requested":           "Demandes de virement",
    "notifications-merchant-email-chargebacks":                   "Oppositions de paiements",
    "notifications-merchant-email-server-notification-failures":  "Échecs de notifications serveurs",
}
# ──────────────────────────────────────────────────────────────────────────────


def ask_config() -> tuple[str, list]:
    """Lit action + liste notifications depuis stdin (2 lignes)."""
    try:
        action_raw = input().strip()   # "activate" ou "deactivate"
        notifs_raw = input().strip()   # JSON array des data-e2e
    except EOFError:
        return "", []

    action = action_raw if action_raw in ("activate", "deactivate") else ""
    try:
        notifs = json.loads(notifs_raw)
        notifs = notifs if isinstance(notifs, list) else []
    except Exception:
        notifs = []

    return action, notifs


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
    except Exception as e:
        print(f"  ⚠ Capture debug échouée : {e}")


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


async def switch_account(page, company_ref: str) -> None:
    """Switch de compte via l'account switcher (même logique que keygen.py)."""
    await page.click(ACCOUNT_SWITCHER_TRIGGER)
    await page.wait_for_selector("[data-e2e^='account-switcher-company-']", timeout=10000)
    company_sel = f"[data-e2e='account-switcher-company-{company_ref}']"
    locator = page.locator(company_sel)
    try:
        await locator.wait_for(timeout=12000)
    except PlaywrightTimeout:
        await page.evaluate("""
            (sel) => {
                const el = document.querySelector(sel);
                if (el) el.scrollIntoView({ block: 'center' });
            }
        """, company_sel)
        await locator.wait_for(timeout=8000)
    await locator.scroll_into_view_if_needed()
    await locator.click()
    await page.wait_for_load_state("domcontentloaded", timeout=15000)


async def _read_checkbox(page, e2e: str):
    """Retourne l'état coché (bool) de la checkbox, ou None si introuvable."""
    loc = page.locator(f'input[data-e2e="{e2e}"]')
    if await loc.count() == 0:
        return None
    return await loc.is_checked()


def _state_str(checked) -> str:
    if checked is None:
        return "introuvable"
    return "cochée" if checked else "décochée"


async def _etat_des_lieux(page, notifications_map: dict) -> dict:
    """Affiche et retourne l'état actuel (coché/décoché) de chaque checkbox connue."""
    etat = {}
    for e2e, label in notifications_map.items():
        checked = await _read_checkbox(page, e2e)
        etat[e2e] = checked
        print(f"    • '{label}' : {_state_str(checked)}")
    return etat


async def _set_checkbox(page, e2e: str, target_checked: bool, label: str, current=None) -> str:
    """
    Met la checkbox à l'état souhaité (ne touche pas à celles déjà conformes).
    `current` permet de réutiliser un état déjà lu (état des lieux) sans relire le DOM.
    Retourne "modifié", "déjà_ok" ou "introuvable".
    """
    is_checked = current if current is not None else await _read_checkbox(page, e2e)

    if is_checked is None:
        print(f"    ⚠ '{label}' introuvable")
        return "introuvable"

    if is_checked == target_checked:
        print(f"    ℹ '{label}' déjà {_state_str(is_checked)} → laissée telle quelle")
        return "déjà_ok"

    # Cliquer le label parent (Ant Design / React)
    await page.locator(f'label:has(input[data-e2e="{e2e}"])').click()
    await asyncio.sleep(0.3)

    action_str = "cochée ✓" if target_checked else "décochée ✗"
    print(f"    ✓ '{label}' {action_str}")
    return "modifié"


async def _confirmer_etat(page, e2e: str, target_checked: bool, label: str) -> bool:
    """Relit la checkbox après sauvegarde et confirme qu'elle est dans l'état attendu."""
    checked = await _read_checkbox(page, e2e)
    if checked == target_checked:
        print(f"    ✓ '{label}' confirmée {_state_str(checked)} après enregistrement")
        return True
    print(f"    ✗ '{label}' état inattendu après enregistrement "
          f"(attendu : {_state_str(target_checked)}, trouvé : {_state_str(checked)})")
    return False


async def process_account(page, company_ref: str, account_name: str,
                          action: str, notifications: list) -> dict:
    result = {
        "company_ref":  company_ref,
        "account_name": account_name,
        "action":       action,
        "notifications": ",".join(notifications),
        "modifiees":    "",
        "status":       "",
        "message":      "",
    }
    target_checked = (action == "activate")

    try:
        await switch_account(page, company_ref)
        print(f"  ✓ Compte actif : {account_name}")

        await page.goto(NOTIF_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Attendre que les checkboxes soient présentes
        await page.wait_for_selector(
            'input[data-e2e^="notifications-"]', timeout=10000
        )

        modifiees = []
        ecarts    = []

        customer_selected = [n for n in notifications if n in CUSTOMER_NOTIFICATIONS]
        merchant_selected = [n for n in notifications if n in MERCHANT_NOTIFICATIONS]

        # ── Section client ─────────────────────────────────────────────────────
        if customer_selected:
            print("  → État des lieux (client) :")
            await _etat_des_lieux(page, CUSTOMER_NOTIFICATIONS)

            for e2e in customer_selected:
                label  = CUSTOMER_NOTIFICATIONS[e2e]
                status = await _set_checkbox(page, e2e, target_checked, label)
                if status == "modifié":
                    modifiees.append(e2e)

            await page.locator(SEL_SUBMIT_CUSTOMER).click()
            await asyncio.sleep(2.5)
            print(f"  ✓ Section client sauvegardée")

            print("  → Vérification de l'état (client) après enregistrement :")
            for e2e in customer_selected:
                label = CUSTOMER_NOTIFICATIONS[e2e]
                ok = await _confirmer_etat(page, e2e, target_checked, label)
                if not ok:
                    ecarts.append(label)

        # ── Section marchand ───────────────────────────────────────────────────
        if merchant_selected:
            print("  → État des lieux (marchand) :")
            await _etat_des_lieux(page, MERCHANT_NOTIFICATIONS)

            for e2e in merchant_selected:
                label  = MERCHANT_NOTIFICATIONS[e2e]
                status = await _set_checkbox(page, e2e, target_checked, label)
                if status == "modifié":
                    modifiees.append(e2e)

            await page.locator(SEL_SUBMIT_MERCHANT).click()
            await asyncio.sleep(2.5)
            print(f"  ✓ Section marchand sauvegardée")

            print("  → Vérification de l'état (marchand) après enregistrement :")
            for e2e in merchant_selected:
                label = MERCHANT_NOTIFICATIONS[e2e]
                ok = await _confirmer_etat(page, e2e, target_checked, label)
                if not ok:
                    ecarts.append(label)

        if ecarts:
            result["status"]  = "ECART"
            result["message"] = "État inattendu après sauvegarde : " + ", ".join(ecarts)
        else:
            result["status"] = "OK"

        result["modifiees"] = ",".join(modifiees)

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, company_ref)

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ {str(e)[:80]}")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    action, notifications = ask_config()
    if not action:
        print("⚠ Action invalide — doit être 'activate' ou 'deactivate'.")
        return
    if not notifications:
        print("⚠ Aucune notification sélectionnée — arrêt.")
        return

    verb = "Activation" if action == "activate" else "Désactivation"
    print(f"  ✓ {verb} de : {', '.join(notifications)}\n")

    print("Chargement du CSV comptes...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "company_ref" not in df.columns:
        print("⚠ Colonne 'company_ref' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["company_ref"])

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seul compte traité.\n")
    else:
        print(f"[PROD] {len(df)} comptes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session(SESSION_FILE))
        page    = await context.new_page()

        await page.goto("https://portal.payplug.com/", wait_until="domcontentloaded")

        for _, row in df.iterrows():
            company_ref  = str(row["company_ref"]).strip()
            account_name = str(row.get("account_name", company_ref)).strip()
            print(f"\n→ [{account_name}] {company_ref}")

            result = await process_account(page, company_ref, account_name, action, notifications)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company_ref", "account_name", "action",
            "notifications", "modifiees", "status", "message",
        ])
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
