"""
CustomPricingBot
────────────────
Pour chaque ID de input/data.csv, sur https://admin.payplug.com/admin/companies/{id},
crée une offre « Platinium » en appliquant, champ par champ, les valeurs saisies sur
l'écran de configuration du dashboard (reprend exactement le flux de création d'offre
d'AdvanceConfigBot — même page, même sélection d'offre — sauf que chaque frais/forfait
est désormais une valeur configurable au lieu d'être systématiquement mis à 0).

Tout champ non coché sur l'écran de configuration est mis à 0 (comportement par défaut
identique à AdvanceConfigBot), comme s'il n'avait pas été personnalisé.

Cases à cocher des moyens de paiement alternatifs (Bancontact, Bizum, Giropay, iDeal,
MyBank, Satispay, Scalapay, Sofort, Wero) : cochées si le pricing saisi (% ou fixe) est
différent de 0€ + 0%, décochées sinon — American Express n'a pas de case à cocher.

Lancement (terminal) :
    python custom_pricing_bot.py
Ou depuis le dashboard (bouton "CustomPricingBot" du bot Admin PayPlug).
Note : en terminal, l'écran de configuration champ par champ n'est pas disponible —
toutes les valeurs sont mises à 0 (utiliser le dashboard pour personnaliser).

DRY_RUN : exécute toute la navigation et le remplissage du formulaire, mais saute le
clic de validation finale (Envoyer l'offre). Active-le avec la variable d'env
`DRY_RUN=1`, ou via le paramètre du dashboard.
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
LOG_FILE    = f"results/results_custom_pricing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

COMPANY_URL = "https://admin.payplug.com/admin/companies/{id}"

SEL_LINK_OFFERS          = 'a[data-e2e="offers-management"]'
SEL_CREATE_OFFER_CARD    = '[data-e2e="create-offer"]'
SEL_OFFER_SELECT_TRIGGER = '#mui-component-select-offer\\.offerName'
SEL_OFFER_NAME_INPUT     = 'input[name="offer.offerName"]'
SEL_OFFER_SUBMIT         = 'button[data-e2e="btnSubmitOffer"]'

OFFER_LABEL = "Platinium"
OFFER_VALUE = "platinium"

CLASSIC_SUFFIXES = [
    "domesticPercentConsumer", "domesticFixedConsumer",
    "eurozonePercentConsumer", "eurozoneFixedConsumer",
    "domesticPercentBusiness", "domesticFixedBusiness",
    "eurozonePercentBusiness", "eurozoneFixedBusiness",
    "offEurozonePercentConsumer", "offEurozoneFixedConsumer",
]
ONEY_SUFFIXES = [
    "withFeesPercent", "withFeesFixed",
    "x3WithoutFeesPercent", "x3WithoutFeesFixed",
    "x4WithoutFeesPercent", "x4WithoutFeesFixed",
]
POS_SUFFIXES = list(CLASSIC_SUFFIXES)  # mêmes libellés que le bloc classic

# Moyens de paiement alternatifs : americanExpress n'a pas de case à cocher.
ALT_PAYMENT_METHODS            = ["americanExpress", "bancontact", "bizum", "giropay",
                                   "ideal", "mybank", "satispay", "scalapay", "sofort", "wero"]
ALT_PAYMENT_METHODS_WITH_CHECKBOX = [m for m in ALT_PAYMENT_METHODS if m != "americanExpress"]

# Même liste de champs qu'AdvanceConfigBot (offre à zéro) — ici chaque champ a une
# valeur par défaut de "0", surchargeable individuellement depuis le dashboard.
PRICING_FIELDS = (
    [f"offer.fees.classic.{s}" for s in CLASSIC_SUFFIXES] +
    [f"offer.fees.oney.{s}" for s in ONEY_SUFFIXES] +
    [f"offer.fees.{m}.{part}" for m in ALT_PAYMENT_METHODS for part in ("percent", "fixed")] +
    ["offer.contract.monthlySubscriptionAmount", "offer.contract.subscriptionFreeTrialDays"] +
    [f"offer.fees.pos.{s}" for s in POS_SUFFIXES] +
    ["offer.contract.monthlySubscriptionAmountPos"]
)
DEFAULT_FIELD_VALUE = "0"

# En DRY_RUN, pause avant de fermer le navigateur pour laisser le temps de vérifier
# visuellement le formulaire rempli (page non envoyée).
DRY_RUN_PAUSE_SECONDS = 12
# ───────────────────────────────────────────────────────────────────────────────


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


def get_field_values() -> dict:
    """Lit les valeurs personnalisées champ par champ (écran de config du dashboard).
    Dashboard (field_selector) : JSON {"offer.fees.classic....": "1.2", ...} sur stdin
    — ne contient que les champs cochés, les autres restent à la valeur par défaut (0).
    Terminal : pas d'écran de config disponible, tout reste à 0."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("Mode terminal : pas d'écran de configuration champ par champ.")
        print("Toutes les valeurs seront mises à 0 (utiliser le dashboard pour personnaliser).")
        print("─────────────────────────────────────────────\n")
        return {}

    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}

    return {
        field: str(value).strip()
        for field, value in data.items()
        if field in PRICING_FIELDS and str(value).strip() != ""
    }


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


async def goto_company(page, company_id: str) -> None:
    await page.goto(COMPANY_URL.format(id=company_id), wait_until="domcontentloaded", timeout=15000)


async def select_offer(page, company_id: str) -> tuple[bool, str]:
    trigger = page.locator(SEL_OFFER_SELECT_TRIGGER)
    await trigger.wait_for(state="visible", timeout=10000)
    await trigger.click()
    await asyncio.sleep(0.5)

    option = page.locator("ul[role='listbox'] li[role='option']").filter(has_text=OFFER_LABEL).first
    if await option.count() == 0:
        return False, f"option '{OFFER_LABEL}' introuvable dans le listbox"
    await option.click()
    await asyncio.sleep(0.3)

    value = await page.locator(SEL_OFFER_NAME_INPUT).input_value()
    if value.strip().lower() != OFFER_VALUE:
        return False, f"offre sélectionnée '{value}' ≠ '{OFFER_VALUE}'"
    return True, "OK"


async def fill_field(page, name: str, value: str) -> tuple[bool, str]:
    loc = page.locator(f'[name="{name}"]')
    if await loc.count() == 0:
        return False, "champ introuvable"

    await loc.fill(value)
    current = await loc.input_value()
    if current == value:
        return True, "OK"

    # Certains champs n'acceptent pas la valeur saisie (ex: "0" refusé) — on retente
    # avec une chaîne vide.
    await loc.fill("")
    current = await loc.input_value()
    if current == "":
        return True, f"vidé ('{value}' refusé)"
    return False, f"valeur inattendue après remplissage : '{current}'"


def _is_nonzero_amount(value: str) -> bool:
    """Un moyen de paiement est considéré actif si son % ou son montant fixe est
    différent de 0. Une valeur non numérique est considérée comme "différente de 0"
    (on ne doit pas désactiver silencieusement un moyen de paiement mal saisi)."""
    try:
        return float(str(value).strip().replace(",", ".")) != 0
    except (TypeError, ValueError):
        return bool(str(value).strip())


async def set_alt_payment_method_checkbox(page, method: str, should_check: bool) -> tuple[bool, str]:
    """Coche ou décoche la case du moyen de paiement selon `should_check` : coché si
    le pricing saisi est différent de 0€ + 0%, décoché si c'est resté à 0."""
    percent_name = f"offer.fees.{method}.percent"
    container = page.locator(f'.MuiGrid-container:has([name="{percent_name}"])').last
    checkbox = container.locator('input.ant-checkbox-input').first

    if await checkbox.count() == 0:
        return False, "case à cocher introuvable"

    is_checked = await checkbox.is_checked()
    if is_checked == should_check:
        return True, "déjà " + ("cochée" if should_check else "décochée")

    label = container.locator('label.ant-checkbox-wrapper').first
    await label.click()
    await asyncio.sleep(0.3)

    is_checked = await checkbox.is_checked()
    if is_checked != should_check:
        return False, ("toujours décochée" if should_check else "toujours cochée") + " après clic"
    return True, "cochée" if should_check else "décochée"


async def configure_offer(page, company_id: str, dry_run: bool, field_values: dict) -> tuple[str, str]:
    await goto_company(page, company_id)
    await page.locator(SEL_LINK_OFFERS).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")
    await page.locator(SEL_CREATE_OFFER_CARD).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")

    ok, msg = await select_offer(page, company_id)
    if not ok:
        await _screenshot_failure(page, company_id, "offer_select")
        return "KO", msg

    field_failures  = []
    customized      = []
    for field in PRICING_FIELDS:
        value = field_values.get(field, DEFAULT_FIELD_VALUE)
        if field in field_values:
            customized.append(f"{field}={value}")
        ok, msg = await fill_field(page, field, value)
        if not ok:
            field_failures.append(f"{field} ({msg})")

    if customized:
        print(f"  ℹ [{company_id}] Champs personnalisés : {', '.join(customized)}")

    checkbox_failures = []
    for method in ALT_PAYMENT_METHODS_WITH_CHECKBOX:
        percent_val  = field_values.get(f"offer.fees.{method}.percent", DEFAULT_FIELD_VALUE)
        fixed_val    = field_values.get(f"offer.fees.{method}.fixed", DEFAULT_FIELD_VALUE)
        should_check = _is_nonzero_amount(percent_val) or _is_nonzero_amount(fixed_val)
        ok, msg = await set_alt_payment_method_checkbox(page, method, should_check)
        if not ok:
            checkbox_failures.append(f"{method} ({msg})")

    if field_failures or checkbox_failures:
        detail = "; ".join(field_failures + checkbox_failures)[:300]
        print(f"  ✗ [{company_id}] Contrôle pré-envoi KO : {detail}")
        await _screenshot_failure(page, company_id, "offer_precheck")
        return "KO", f"contrôle pré-envoi échoué : {detail}"

    # ── Contrôle avant envoi (relecture) ──────────────────────────────────────
    value = await page.locator(SEL_OFFER_NAME_INPUT).input_value()
    if value.strip().lower() != OFFER_VALUE:
        await _screenshot_failure(page, company_id, "offer_precheck")
        return "KO", f"offre '{value}' ≠ '{OFFER_VALUE}' au contrôle final"

    if dry_run:
        print(f"  ⏭ [{company_id}] SIMULÉ (DRY_RUN, offre non envoyée)")
        return "OK", f"simulé (dry_run) — {len(customized)} champ(s) personnalisé(s)"

    submit = page.locator(SEL_OFFER_SUBMIT)
    if await submit.count() == 0:
        return "KO", "bouton 'Envoyer l'offre' introuvable"
    await submit.click()
    await asyncio.sleep(2)
    print(f"  ✓ [{company_id}] Offre Platinium créée")
    return "OK", f"offre créée — {len(customized)} champ(s) personnalisé(s)"


async def process_company(page, company_id: str, dry_run: bool, field_values: dict) -> dict:
    try:
        status, message = await configure_offer(page, company_id, dry_run, field_values)
    except PlaywrightTimeout as e:
        status, message = "KO", f"timeout : {str(e)[:120]}"
        await _screenshot_failure(page, company_id, "configure_offer")
    except Exception as e:
        status, message = "KO", str(e)[:120]
        await _screenshot_failure(page, company_id, "configure_offer")

    print(f"[{company_id}] {status}{' (' + message + ')' if message else ''}")
    return {"id": company_id, "status": status, "message": message}


async def main():
    os.makedirs("results", exist_ok=True)

    dry_run = get_dry_run()
    print(f"DRY_RUN = {dry_run}\n")

    field_values = get_field_values()
    if field_values:
        print(f"{len(field_values)} champ(s) personnalisé(s) reçu(s) du dashboard.\n")
    else:
        print("Aucun champ personnalisé — tous les frais/forfaits seront mis à 0.\n")

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
        print(f"[PROD] {len(df)} comptes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            company_id = str(row["id"]).strip()
            print(f"\n→ Traitement {company_id}")
            result = await process_company(page, company_id, dry_run, field_values)
            results.append(result)
            await asyncio.sleep(1.0)

        if dry_run:
            print(f"\n⏸ DRY_RUN — pause de {DRY_RUN_PAUSE_SECONDS}s avant fermeture du navigateur (vérification visuelle)...")
            await asyncio.sleep(DRY_RUN_PAUSE_SECONDS)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "status", "message"])
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
