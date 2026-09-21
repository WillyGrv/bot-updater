"""
FinancialAdvanceBot
────────────────────
Pour chaque UDV de input/financial.csv, sur https://internal-payment.gcp.dlns.io/cockpit/#/financialAccount :
  1. Clique sur "ADD" pour ouvrir le formulaire de création.
  2. Renseigne udv (F{ID_UDV}), tradeName et name ("Payplug retail - {Nom UDV}").
  3. Renseigne les champs de configuration fixes (adresse, fiscal, financier, banque).
  4. Soumet le formulaire ("Save changes").

Lancement (terminal) :
    python financial_advance_bot.py
Ou depuis le dashboard (bouton "FinancialAdvanceBot" du bot Cockpit PayPlug).

DRY_RUN : remplit tout le formulaire mais ne clique pas sur "Save changes".
Active-le avec la variable d'env `DRY_RUN=1`, ou via le paramètre du dashboard.
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
DATA_SOURCE = "input/financial.csv"
LOG_FILE    = f"results/results_financial_advance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

BASE_COCKPIT_URL      = "https://internal-payment.gcp.dlns.io/cockpit/"
FINANCIAL_ACCOUNT_URL = "https://internal-payment.gcp.dlns.io/cockpit/#/financialAccount"

TRADE_NAME_PREFIX = "Payplug retail - "

# Champs propres au marchand : udv = F{id}, tradeName/name = "Payplug retail - {nom}"
# (construits dynamiquement, voir build_row_fields)

# Champs texte fixes — identiques pour chaque UDV.
# idDefaultLanguage/EN, idBillingCurrency/EUR, minimumBalance/0 et businessDayCutOff/00:00:00
# sont déjà pré-remplis avec la bonne valeur par défaut à l'ouverture du formulaire (ADD) —
# volontairement ignorés ici pour simplifier (un champ en moins à toucher = un point de
# défaillance en moins).
FIXED_TEXT_FIELDS = {
    "address":                     "110 AV DE FRANCE",
    "zipCode":                     "75013",
    "city":                        "Paris",
    "building":                    "",
    "locality":                    "",
    "name2T24":                    "5890317",
    "siret":                       "75165888100030",
    "vatNumber":                   "FR01751658881",
    "remittanceInfoLabel":         "",
    "allowAdvanceActivationDate":  "",
    "guaranteePercent":            "0",
    "paymentThreshold":            "0",
    "chargebackWarningRate":       "1",
}

# Section Banks — repliée par défaut, traitée à part et en dernier (juste avant la
# soumission) car son bouton "+" est tout en bas d'un long formulaire.
BANK_FIELDS = {
    "paymentInformation.0.bank":   "NATXFRPPXXX",
    "paymentInformation.0.iban":   "FR7630007999990650067400276",
    "paymentInformation.0.ratio":  "100",
}

# Listes MUI Select — fixes pour chaque UDV. "label" est cherché en substring dans
# le listbox (gère aussi les libellés tronqués comme idLegalCategoryII).
SELECT_FIELDS = {
    "idCountry":                    {"label": "France",                                   "value": "FR"},
    "idNationality":                {"label": "France",                                   "value": "FR"},
    "idActivityCountry":            {"label": "France",                                   "value": "FR"},
    "idTaxResidenceCountry":        {"label": "France",                                   "value": "FR"},
    "idClearingBank":               {"label": "NATIXIS",                                  "value": "999999"},
    "idSector":                     {"label": "5100 - Banque centrale",                   "value": "5100"},
    "idLegalCategoryII":            {"label": "51 - Societe cooperative commerciale par", "value": "51"},
    "nafCode":                      {"label": "6201Z - Programmation informatique",       "value": "6201Z"},
    "vatMode":                      {"label": "1.TAUX.NORMAL",                            "value": "classic"},
    # DAO1 est le tout premier item de la liste : la frappe "D" n'est pas nécessaire
    # (et déroute la recherche) — un simple ArrowDown depuis l'ouverture suffit.
    "idMainAccountManager":         {"label": "DAO1", "value": "1", "skip_letter": True},
    "idSecondaryAccountManager":    {"label": "Implementation",                           "value": "200"},
    "idEconomicAgentCode":          {"label": "115 - BANQUES",                            "value": "115"},
    "fundTransferFrequency":        {"label": "3 - quotidien",                            "value": "DAILY"},
    "idGuaranteeTransferFrequency": {"label": "None",                                     "value": "0"},
    "dateFormatSepaLabel":          {"label": "JJ/MM/AAAA",                               "value": "1"},
}

# Switches MUI — état cible fixe pour chaque UDV. Clic conditionnel uniquement.
SWITCH_FIELDS = {
    "blockedTransfer": False,
    "allowAdvance":    True,
}

SEL_ADD_BUTTON = 'button:text-is("add")'
SEL_UDV_FIELD    = 'input[name="udv"]'
SEL_ADD_BANK_ROW = 'button:has-text("+")'
SEL_SUBMIT       = 'form button[type="submit"]'
# ──────────────────────────────────────────────────────────────────────────────


def get_dry_run() -> bool:
    """Priorité : variable d'env DRY_RUN, sinon paramètre dashboard (stdin), sinon prompt manuel."""
    env_val = os.environ.get("DRY_RUN", "").strip().lower()
    if env_val in ("1", "true", "yes", "oui"):
        return True
    if env_val in ("0", "false", "no", "non"):
        return False
    if sys.stdin.isatty():
        try:
            raw = input("DRY_RUN ? (o/N) : ").strip().lower()
        except EOFError:
            raw = ""
        return raw in ("o", "oui", "y", "yes")
    try:
        raw = input().strip().lower()
    except EOFError:
        raw = ""
    return raw in ("yes", "oui", "1", "true")


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


def build_row_fields(udv_id: str, nom_udv: str) -> dict:
    label = f"{TRADE_NAME_PREFIX}{nom_udv}"
    return {
        "udv":       f"F{udv_id}",
        "tradeName": label,
        "name":      label,
    }


async def _find_frame(page, selector: str):
    """Cherche le frame contenant le sélecteur (iframe possible — même pattern que
    cockpit_mid_updater.py / check_additional_fees_udv_bot.py sur cette même appli)."""
    for f in page.frames:
        try:
            if await f.locator(selector).count() > 0:
                return f
        except Exception:
            pass
    return None


async def fill_text_field(page, field_name: str, value: str) -> tuple[bool, str]:
    loc = page.locator(f'[name="{field_name}"]')
    if await loc.count() == 0:
        return False, "champ introuvable"
    await loc.fill(value)
    actual = (await loc.input_value()).strip()
    if actual != value:
        return False, f"valeur '{actual}' ≠ '{value}' attendue"
    return True, "OK"


async def select_mui_option(page, frame, field_name: str, label: str, expected_value: str, skip_letter: bool = False) -> tuple[bool, str]:
    # `page` : objet Page top-level, seul détenteur du clavier (`.keyboard`), même si les
    # champs vivent dans un iframe. `frame` : Page ou Frame utilisé pour localiser les éléments.
    # L'input porteur du name est caché — on cible le div.MuiSelect-root frère,
    # localisé via le conteneur .MuiInputBase-root commun (cf. règle de ciblage #4).
    container = frame.locator(f'.MuiInputBase-root:has(input[name="{field_name}"])')
    trigger   = container.locator('div[role="button"].MuiSelect-root')
    if await trigger.count() == 0:
        return False, "trigger MUI Select introuvable"

    await trigger.click()
    await asyncio.sleep(0.3)

    # Séquence validée manuellement : 1ère lettre du libellé (jump typeahead), puis
    # ArrowDown répété jusqu'à ce que l'option surlignée corresponde, puis Entrée.
    # Le typeahead ne semble PAS accumuler les lettres tapées en continu (essayé et
    # confirmé faux), donc on n'appuie qu'une fois sur la 1ère lettre puis on navigue
    # par pas, en revérifiant la cible à chaque étape plutôt que de deviner un nombre
    # fixe de flèches (qui ne vaudrait que pour ce champ précis).
    # L'option "surlignée" par les flèches n'est pas identifiable par un attribut/classe
    # statique (tabindex="0" testé, ne correspond pas à l'élément réellement ciblé) —
    # MUI déplace le vrai focus DOM sur l'item courant, donc on interroge
    # document.activeElement directement, dans le frame du formulaire.
    # skip_letter : pour les champs où la cible est déjà le tout premier item de la
    # liste (ex. idMainAccountManager/DAO1) — la frappe de lettre est inutile et peut
    # dérouter la recherche (plusieurs items partagent le même préfixe).
    if not skip_letter:
        await page.keyboard.press(label[0])
        await asyncio.sleep(0.3)

    matched = False
    for _ in range(60):
        try:
            text = await frame.evaluate("() => document.activeElement ? document.activeElement.textContent : ''")
        except Exception:
            text = ""
        text = (text or "").strip()
        if label in text:
            matched = True
            break
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.15)

    if not matched:
        if await page.locator("ul[role='listbox']").count() > 0:
            await page.keyboard.press("Escape")
        return False, f"option '{label}' non atteinte via navigation clavier (60 pas max)"

    await page.keyboard.press("Enter")
    await asyncio.sleep(0.2)

    hidden_input = frame.locator(f'input[name="{field_name}"]')
    value = (await hidden_input.input_value()).strip()
    if value != expected_value:
        return False, f"valeur '{value}' ≠ '{expected_value}' attendue après saisie clavier '{label}'"
    return True, "OK"


async def set_switch(page, field_name: str, desired: bool) -> tuple[bool, str]:
    switch_input = page.locator(f'input[name="{field_name}"].MuiSwitch-input')
    if await switch_input.count() == 0:
        return False, "switch introuvable"

    current = await switch_input.is_checked()
    if current == desired:
        return True, "déjà dans l'état souhaité"

    # Clic conditionnel uniquement — jamais de toggle aveugle.
    await switch_input.click()
    await asyncio.sleep(0.2)

    current = await switch_input.is_checked()
    if current != desired:
        return False, "clic sans effet — état inchangé"
    return True, "OK"


async def process_udv(page, udv_id: str, nom_udv: str, dry_run: bool) -> dict:
    result = {"id": udv_id, "nom_udv": nom_udv, "status": "", "message": ""}

    try:
        await page.goto(BASE_COCKPIT_URL, wait_until="domcontentloaded", timeout=15000)
        await page.goto(FINANCIAL_ACCOUNT_URL, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Le listing (et potentiellement le formulaire) peut vivre dans un iframe —
        # même appli que cockpit_mid_updater.py / check_additional_fees_udv_bot.py.
        list_frame = await _find_frame(page, SEL_ADD_BUTTON)
        if list_frame is None:
            await _screenshot_failure(page, udv_id, "add_button")
            result.update(status="KO", message="bouton ADD introuvable dans tous les frames")
            return result

        add_button = list_frame.locator(SEL_ADD_BUTTON).first
        await add_button.click()
        await asyncio.sleep(1.5)

        # Le formulaire ouvert par le clic peut être dans un frame différent du listing.
        form_frame = await _find_frame(page, SEL_UDV_FIELD)
        if form_frame is None:
            await _screenshot_failure(page, udv_id, "form_open")
            result.update(status="KO", message="formulaire de création introuvable après clic ADD")
            return result

        failures = []

        for field, value in build_row_fields(udv_id, nom_udv).items():
            ok, msg = await fill_text_field(form_frame, field, value)
            if not ok:
                failures.append(f"{field} ({msg})")

        for field, value in FIXED_TEXT_FIELDS.items():
            ok, msg = await fill_text_field(form_frame, field, value)
            if not ok:
                failures.append(f"{field} ({msg})")

        for field, cfg in SELECT_FIELDS.items():
            ok, msg = await select_mui_option(page, form_frame, field, cfg["label"], cfg["value"], cfg.get("skip_letter", False))
            if not ok:
                failures.append(f"{field} ({msg})")

        for field, desired in SWITCH_FIELDS.items():
            ok, msg = await set_switch(form_frame, field, desired)
            if not ok:
                failures.append(f"{field} ({msg})")

        # Section Banks — traitée en dernier : repliée par défaut, son bouton "+" est
        # tout en bas d'un long formulaire et n'a pas besoin d'être révélé plus tôt.
        # hover() sur "udv" (tout en haut) auto-scrollait la page en arrière avant de
        # scroller vers le bas — on survole plutôt le dernier champ déjà traité
        # (allowAdvance, juste avant Banks) pour ne pas annuler la descente déjà faite,
        # et on scrolle nettement plus loin pour être sûr d'atteindre le bas.
        try:
            await form_frame.locator('input[name="allowAdvance"]').hover()
        except Exception:
            pass
        for _ in range(15):
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(0.2)

        # :text-is("+") exige une égalité exacte (sensible à un espace invisible dans le
        # <span> par ex.) et ne cherchait que dans form_frame — on élargit à un match
        # substring, cherché dans tous les frames (le bouton est peut-être visuellement
        # dans le même formulaire mais techniquement dans un frame différent).
        bank_frame = await _find_frame(page, SEL_ADD_BANK_ROW)
        if bank_frame is None:
            failures.append("bouton '+' Banks introuvable (dans aucun frame)")
        else:
            add_bank_row = bank_frame.locator(SEL_ADD_BANK_ROW)
            bank_row_count = await add_bank_row.count()
            if bank_row_count == 1:
                await add_bank_row.scroll_into_view_if_needed()
                await asyncio.sleep(0.2)
                await add_bank_row.evaluate("el => el.click()")
                await asyncio.sleep(0.5)
            else:
                failures.append(f"bouton '+' Banks ambigu ({bank_row_count} correspondances)")

        for field, value in BANK_FIELDS.items():
            ok, msg = await fill_text_field(form_frame, field, value)
            if not ok:
                failures.append(f"{field} ({msg})")

        if failures:
            detail = "; ".join(failures)[:400]
            print(f"  ✗ [{udv_id}] Champ(s) en échec : {detail}")
            await _screenshot_failure(page, udv_id, "form")
            result.update(status="KO", message=detail)
            return result

        if dry_run:
            print(f"  ⏭ [{udv_id}] SIMULÉ (DRY_RUN) — formulaire rempli, non soumis")
            result.update(status="OK", message="simulé (dry_run)")
            return result

        submit = form_frame.locator(SEL_SUBMIT)
        if await submit.count() == 0:
            submit = form_frame.locator("form button").filter(has_text="Save changes").first
        if await submit.count() == 0:
            result.update(status="KO", message="bouton 'Save changes' introuvable")
            await _screenshot_failure(page, udv_id, "submit")
            return result

        await submit.click()
        await asyncio.sleep(2)

        # Détection best-effort d'une erreur serveur (SIRET/IBAN déjà utilisé, etc.) —
        # sélecteur non confirmé en conditions réelles, à ajuster après un premier run.
        error_banner = form_frame.locator('[role="alert"], .MuiAlert-standardError, .Toastify__toast--error')
        if await error_banner.count() > 0:
            err_text = (await error_banner.first.inner_text())[:200]
            result.update(status="KO", message=f"erreur serveur : {err_text}")
            print(f"  ✗ [{udv_id}] Erreur serveur : {err_text}")
            await _screenshot_failure(page, udv_id, "server_error")
            return result

        print(f"  ✓ [{udv_id}] Financial Account créé")
        result.update(status="OK", message="créé")

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

    dry_run = get_dry_run()
    print(f"DRY_RUN = {dry_run}\n")

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id udv" not in df.columns or "nom udv" not in df.columns:
        print("⚠ Colonnes 'ID udv' et 'Nom UDV' introuvables dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["id udv", "nom udv"])

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
            udv_id  = str(row["id udv"]).strip()
            nom_udv = str(row["nom udv"]).strip()
            print(f"\n→ Traitement UDV {udv_id} ({nom_udv})")
            result = await process_udv(page, udv_id, nom_udv, dry_run)
            results.append(result)
            await asyncio.sleep(1.0)

        if TEST_MODE:
            print("\n🔍 Navigateur laissé ouvert pour inspection (TEST_MODE).")
            if sys.stdin.isatty():
                input("   Appuie sur Entrée ici pour le fermer... ")
            else:
                print("   ⚠ stdin non-interactif détecté — pause impossible, fermeture immédiate.")

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "nom_udv", "status", "message"])
        writer.writeheader()
        writer.writerows(results)

    ok     = sum(1 for r in results if r["status"] == "OK")
    failed = [r["id"] for r in results if r["status"] != "OK"]
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}/{len(results)}")
    print(f"✗ Échecs  : {len(failed)}")
    if failed:
        print(f"  IDs en échec : {', '.join(failed)}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
