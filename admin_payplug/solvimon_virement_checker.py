"""
Solvimon Virement
──────────────────
Pour chaque ligne de input/data.csv, deux actions indépendantes (une ou les deux,
au choix) :

  - check    : vérifie la présence du bloc Solvimon (Documents comptables) et lit
               le statut des virements (Configuration) — comportement inchangé
               par rapport à l'ancien CheckAdvanceBusinessDayBot.
  - activate : sur la même page Configuration, si le statut est "Non activé",
               sélectionne le délai de règlement choisi (J+1 à J+7, facultatif)
               puis clique "Oui" et confirme l'activation du business day.
               ⚠ Toute activation est définitive — n'est jamais déclenchée par
               défaut (aucune sélection = check seul, jamais activate).
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
DATA_SOURCE = "input/data.csv"
LOG_FILE    = f"results/solvimon_virement_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

BASE_URL = "https://admin.payplug.com/admin/companies/{id}"

SOLVIMON_TEXT = "Ce marchand est facturé via Solvimon"

SEL_ACCOUNTING_LINK       = 'a[data-e2e="accounting"]'
SEL_TRANSFERS_LINK        = 'a[data-e2e="queued-transfers"][href^="/bank-transfers-company/"]'
SEL_CONFIG_LINK           = 'a[id="configuration"]'
SEL_VIREMENT_STATUS       = '[data-e2e="business-day-status"]'
SEL_BUSINESS_DAY_RADIO_YES = 'input[data-e2e="business-day-radio-yes"]'
SEL_SETTLEMENT_SELECT      = '[data-e2e="settlement-delay-select"]'
SEL_SETTLEMENT_CONFIRM     = 'button[data-e2e="business-day-confirm-button"]'

ALL_ACTIONS     = {"check", "activate"}
DEFAULT_ACTIONS = {"check"}

SETTLEMENT_DELAY_OPTIONS = {f"j{n}": f"J+{n}" for n in range(1, 8)}
# ──────────────────────────────────────────────────────────────────────────────


def get_selected_actions() -> set:
    """Lit les actions à exécuter (plusieurs choix possibles).
    Dashboard (checkbox_group) : JSON ["check", "activate"] sur stdin.
    Terminal : numéros séparés par des virgules. Par défaut (vide/invalide) :
    check seul — l'activation du business day est définitive, elle ne doit
    jamais se déclencher par défaut."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("ACTIONS À EXÉCUTER (plusieurs choix possibles)")
        print("─────────────────────────────────────────────")
        print("  [1] Check (Solvimon + statut virement)")
        print("  [2] Activation Business day")
        raw = input("  Numéros séparés par des virgules (défaut: 1) : ").strip()
        print("─────────────────────────────────────────────\n")
        mapping = {"1": "check", "2": "activate"}
        chosen  = {mapping[p.strip()] for p in raw.split(",") if p.strip() in mapping}
        return chosen or set(DEFAULT_ACTIONS)

    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    try:
        chosen = {a for a in json.loads(raw) if a in ALL_ACTIONS}
    except Exception:
        chosen = set()
    return chosen or set(DEFAULT_ACTIONS)


def get_settlement_delay() -> str:
    """Délai de règlement à appliquer en cas d'activation. "keep" = ne pas toucher
    au délai actuellement configuré. Dashboard (radio) : valeur brute sur stdin."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("DÉLAI DE RÈGLEMENT (appliqué uniquement si activation)")
        print("─────────────────────────────────────────────")
        print("  [0] Ne pas changer")
        for i in range(1, 8):
            print(f"  [{i}] J+{i}")
        raw = input("  Votre choix (défaut: 0) : ").strip()
        print("─────────────────────────────────────────────\n")
        return f"j{raw}" if raw in {str(n) for n in range(1, 8)} else "keep"

    try:
        raw = input().strip().lower()
    except EOFError:
        raw = ""
    return raw if raw in SETTLEMENT_DELAY_OPTIONS else "keep"


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


async def _click_radio_by_input(page, input_selector: str) -> bool:
    """Clique sur un radio Ant Design : l'input natif est masqué visuellement, on
    clique donc sur son label englobant (lui est cliquable)."""
    label = page.locator(f'label:has({input_selector})')
    if await label.count() == 0:
        return False
    await label.first.click()
    return True


async def _select_settlement_delay(page, delay_key: str, row_id: str = "") -> tuple[bool, str]:
    if delay_key == "keep" or not delay_key:
        return True, "inchangé"

    label = SETTLEMENT_DELAY_OPTIONS.get(delay_key)
    if not label:
        return False, f"délai inconnu : {delay_key}"

    trigger = page.locator(SEL_SETTLEMENT_SELECT)
    if await trigger.count() == 0:
        return False, "sélecteur de délai introuvable"

    # Le panneau de la liste (.ant-select-dropdown) est rendu dans un portail en fin
    # de <body>, pas sous le trigger — et un panneau précédemment ouvert (display:
    # none) peut rester dans le DOM, d'où le filtre ":visible" (même pattern que
    # realm_owner_setter.py pour ce composant Ant Design). On attend l'option plutôt
    # que de deviner un délai fixe, le temps d'ouverture/rendu variant.
    option = page.locator(f'.ant-select-dropdown:visible .ant-select-item-option[title="{label}"]')

    await trigger.click()
    try:
        await option.first.wait_for(state="visible", timeout=4000)
    except PlaywrightTimeout:
        # Le clic sur le conteneur ne déclenche pas toujours l'ouverture (composant
        # stylé maison au-dessus d'Ant Design) — on retente sur l'input interne
        # (role combobox), cible réelle du handler dans certaines variantes.
        try:
            await trigger.locator("input").click()
            await option.first.wait_for(state="visible", timeout=4000)
        except PlaywrightTimeout:
            await _screenshot_timeout(page, f"settlement_delay_{row_id or 'row'}")
            return False, f"option '{label}' introuvable dans la liste déroulante (délai d'ouverture dépassé)"

    await option.first.click()
    await asyncio.sleep(0.3)

    trigger_text = (await trigger.inner_text()).strip()
    if label not in trigger_text:
        return False, f"délai affiché '{trigger_text}' ≠ '{label}' attendu"
    return True, "OK"


async def _activate_business_day(page, settlement_delay: str, row_id: str = "") -> tuple[str, str]:
    """Clique 'Oui', sélectionne le délai de règlement (facultatif) puis confirme.
    Ne doit être appelé que si le statut actuel est 'Non activé' — l'activation
    est définitive.
    Ordre important : le select de délai est vraisemblablement inerte tant que
    l'activation n'est pas engagée (clic sur 'Oui' en premier)."""
    if not await _click_radio_by_input(page, SEL_BUSINESS_DAY_RADIO_YES):
        return "KO", "radio 'Oui' introuvable"
    await asyncio.sleep(0.3)

    yes_input = page.locator(SEL_BUSINESS_DAY_RADIO_YES)
    if not await yes_input.is_checked():
        return "KO", "clic sur 'Oui' sans effet"

    delay_ok, delay_msg = await _select_settlement_delay(page, settlement_delay, row_id)
    if not delay_ok:
        return "KO", delay_msg

    confirm = page.locator(SEL_SETTLEMENT_CONFIRM)
    if await confirm.count() == 0:
        return "KO", "bouton 'Confirmer la mise à jour' introuvable"
    await confirm.click()
    await asyncio.sleep(2)

    detail = f"activé (délai {SETTLEMENT_DELAY_OPTIONS[settlement_delay]})" if settlement_delay in SETTLEMENT_DELAY_OPTIONS else "activé"
    return "OK", detail


async def check_row(page, row_id: str, url: str, actions: set, settlement_delay: str) -> dict:
    result = {
        "id": row_id,
        "url": url,
        "solvimon": "",
        "virement_statut": "",
        "activation_status": "",
        "activation_message": "",
        "status": "",
        "message": "",
    }

    try:
        # ── 1. Page principale ──────────────────────────────────────────────
        await page.goto(url, wait_until="networkidle", timeout=20000)

        # ── 2. Documents comptables (uniquement si "check" sélectionné) ─────
        if "check" in actions:
            await page.wait_for_selector(SEL_ACCOUNTING_LINK, timeout=10000)
            await page.click(SEL_ACCOUNTING_LINK)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

            body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            solvimon_present = SOLVIMON_TEXT in body_text
            result["solvimon"] = "PRÉSENT" if solvimon_present else "MANQUANT"
            print(f"  {'✓' if solvimon_present else '✗'} [{row_id}] Solvimon → {result['solvimon']}")

            # ── 3. Retour page principale ───────────────────────────────────
            await page.goto(url, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(1)
        else:
            result["solvimon"] = "NON DEMANDÉ"

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
        # Extrait "Activé" ou "Non activé" depuis "Statut: Non activé"
        statut = status_text.replace("Statut:", "").strip().split("\n")[-1].strip()
        result["virement_statut"] = statut
        print(f"  ✓ [{row_id}] Statut virement → {statut}")

        # ── 7. Activation Business day (uniquement si "activate" sélectionné) ──
        if "activate" in actions:
            if "non" in statut.lower():
                act_status, act_message = await _activate_business_day(page, settlement_delay, row_id)
                result["activation_status"]  = act_status
                result["activation_message"] = act_message
                print(f"  {'✓' if act_status == 'OK' else '✗'} [{row_id}] Activation Business day → {act_status} ({act_message})")
            else:
                result["activation_status"]  = "SKIP"
                result["activation_message"] = "déjà activé"
                print(f"  ⋯ [{row_id}] Activation ignorée — déjà activé")
        else:
            result["activation_status"]  = "SKIP"
            result["activation_message"] = "non sélectionné"

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

    actions          = get_selected_actions()
    settlement_delay = get_settlement_delay()
    print(f"Actions sélectionnées : {', '.join(sorted(actions))}")
    if "activate" in actions:
        print(f"Délai de règlement : {SETTLEMENT_DELAY_OPTIONS.get(settlement_delay, 'inchangé')}")
    print()

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
            result = await check_row(page, row_id, url, actions, settlement_delay)
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    fieldnames = [
        "id", "url", "solvimon", "virement_statut",
        "activation_status", "activation_message", "status", "message",
    ]
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
