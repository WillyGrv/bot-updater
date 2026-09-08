"""
AdvanceConfigBot
────────────────
Pour chaque ID de input/data.csv, sur https://admin.payplug.com/admin/companies/{id} :
  1. Active le module « Display report creator » (page Fonctionnalités).
  2. Crée les feature flags listés dans FEATURE_FLAGS (page Feature flags).
  3. Crée une offre « Platinium » avec tous les frais/forfaits à zéro (page Offres).

Lancement (terminal) :
    python advance_config_bot.py
Ou depuis le dashboard (bouton "AdvanceConfigBot" du bot Admin PayPlug).

DRY_RUN : exécute toute la navigation et le remplissage des formulaires, mais
saute les clics de validation finale (Enregistrer / Créer / Envoyer l'offre).
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
DATA_SOURCE = "input/data.csv"
LOG_FILE    = f"results/results_advance_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

COMPANY_URL = "https://admin.payplug.com/admin/companies/{id}"

SEL_LINK_FEATURES      = 'a[data-e2e="features"]'
SEL_LINK_FEATURE_FLAGS = 'a[data-e2e="manage-features-flags"]'
SEL_LINK_OFFERS        = 'a[data-e2e="offers-management"]'

SEL_SUBMIT_FEATURES = 'button[data-e2e="merchant-features-btn-submit"]'
SEL_MODULE_ROW       = 'div[data-e2e="merchant-features-{value}-admin-feature"]'

SEL_FLAG_INPUT  = 'input[data-e2e="new-flag-name-input"]'
SEL_FLAG_SUBMIT = 'button[data-e2e="new-feature-flag-submit"]'

SEL_CREATE_OFFER_CARD  = '[data-e2e="create-offer"]'
SEL_OFFER_SELECT_TRIGGER = '#mui-component-select-offer\\.offerName'
SEL_OFFER_NAME_INPUT     = 'input[name="offer.offerName"]'
SEL_OFFER_SUBMIT         = 'button[data-e2e="btnSubmitOffer"]'

MODULE_TO_ACTIVATE = {"value": "displayReportCreator", "label": "Display report creator"}

FEATURE_FLAGS = [
    "is_enabled_for_operation_consumer_orphan_refunds",
    "newApiKeysPageActivated",
]

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
ALT_PAYMENT_METHODS           = ["americanExpress", "bancontact", "bizum", "giropay",
                                  "ideal", "mybank", "satispay", "scalapay", "sofort", "wero"]
ALT_PAYMENT_METHODS_TO_UNCHECK = [m for m in ALT_PAYMENT_METHODS if m != "americanExpress"]

FIELDS_TO_ZERO = (
    [f"offer.fees.classic.{s}" for s in CLASSIC_SUFFIXES] +
    [f"offer.fees.oney.{s}" for s in ONEY_SUFFIXES] +
    [f"offer.fees.{m}.{part}" for m in ALT_PAYMENT_METHODS for part in ("percent", "fixed")] +
    ["offer.contract.monthlySubscriptionAmount", "offer.contract.subscriptionFreeTrialDays"] +
    [f"offer.fees.pos.{s}" for s in POS_SUFFIXES] +
    ["offer.contract.monthlySubscriptionAmountPos"]
)
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


# ─── Action 1 — Module « Display report creator » ────────────────────────────
async def action1_activate_module(page, company_id: str, dry_run: bool) -> tuple[str, str]:
    await goto_company(page, company_id)
    await page.locator(SEL_LINK_FEATURES).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")

    row      = page.locator(SEL_MODULE_ROW.format(value=MODULE_TO_ACTIVATE["value"]))
    checkbox = row.locator('input.ant-checkbox-input')
    try:
        await row.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeout:
        await _screenshot_failure(page, company_id, "action1_module")
        return "KO", "checkbox 'Display report creator' introuvable (page non chargée après 10s)"

    if await checkbox.is_checked():
        print(f"  ℹ [{company_id}] Action 1 — module déjà actif")
        return "OK", "déjà actif"

    label = row.locator('label.ant-checkbox-wrapper')
    await label.click()
    await asyncio.sleep(0.4)

    if not await checkbox.is_checked():
        return "KO", "clic sans effet — checkbox toujours décochée"

    if dry_run:
        print(f"  ⏭ [{company_id}] Action 1 — SIMULÉ (DRY_RUN, pas de clic Enregistrer)")
        return "OK", "simulé (dry_run)"

    submit = page.locator(SEL_SUBMIT_FEATURES)
    if await submit.count() == 0:
        return "KO", "bouton Enregistrer introuvable"
    await submit.click()
    await asyncio.sleep(1.5)
    print(f"  ✓ [{company_id}] Action 1 — module activé")
    return "OK", "activé"


# ─── Action 2 — Feature flags ─────────────────────────────────────────────────
async def action2_feature_flags(page, company_id: str, dry_run: bool) -> tuple[str, str]:
    await goto_company(page, company_id)
    await page.locator(SEL_LINK_FEATURE_FLAGS).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(1)

    created, already, failed = [], [], []

    for flag in FEATURE_FLAGS:
        existing = page.get_by_text(flag, exact=True)
        if await existing.count() > 0:
            print(f"  ℹ [{company_id}] flag '{flag}' déjà présent")
            already.append(flag)
            continue

        try:
            field = page.locator(SEL_FLAG_INPUT)
            await field.fill("")
            await field.fill(flag)

            if dry_run:
                print(f"  ⏭ [{company_id}] flag '{flag}' — SIMULÉ (DRY_RUN)")
                await field.fill("")
                continue

            await page.locator(SEL_FLAG_SUBMIT).click()
            await asyncio.sleep(1.5)
            print(f"  ✓ [{company_id}] flag '{flag}' créé")
            created.append(flag)
        except Exception as e:
            msg = str(e)[:120]
            if "duplicate" in msg.lower() or "already" in msg.lower() or "existe" in msg.lower():
                print(f"  ℹ [{company_id}] flag '{flag}' — doublon ignoré")
                already.append(flag)
            else:
                print(f"  ✗ [{company_id}] flag '{flag}' — {msg}")
                failed.append(flag)

    if failed:
        return "KO", f"échecs : {', '.join(failed)}"
    return "OK", f"créés={len(created)}, déjà présents={len(already)}"


# ─── Action 3 — Offre Platinium à zéro ────────────────────────────────────────
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


async def zero_field(page, name: str) -> tuple[bool, str]:
    loc = page.locator(f'[name="{name}"]')
    if await loc.count() == 0:
        return False, "champ introuvable"

    await loc.fill("0")
    value = await loc.input_value()
    if value == "0":
        return True, "OK"

    # Certains champs n'acceptent pas "0" — on retente avec une chaîne vide.
    await loc.fill("")
    value = await loc.input_value()
    if value == "":
        return True, "vidé (0 refusé)"
    return False, f"valeur inattendue après remplissage : '{value}'"


async def uncheck_alt_payment_method(page, method: str) -> tuple[bool, str]:
    percent_name = f"offer.fees.{method}.percent"
    container = page.locator(f'.MuiGrid-container:has([name="{percent_name}"])').last
    checkbox = container.locator('input.ant-checkbox-input').first

    if await checkbox.count() == 0:
        return False, "case à cocher introuvable"
    if not await checkbox.is_checked():
        return True, "déjà décochée"

    label = container.locator('label.ant-checkbox-wrapper').first
    await label.click()
    await asyncio.sleep(0.3)

    if await checkbox.is_checked():
        return False, "toujours cochée après clic"
    return True, "décochée"


async def action3_create_offer(page, company_id: str, dry_run: bool) -> tuple[str, str]:
    await goto_company(page, company_id)
    await page.locator(SEL_LINK_OFFERS).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")
    await page.locator(SEL_CREATE_OFFER_CARD).click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")

    ok, msg = await select_offer(page, company_id)
    if not ok:
        await _screenshot_failure(page, company_id, "offer_select")
        return "KO", msg

    field_failures = []
    for field in FIELDS_TO_ZERO:
        ok, msg = await zero_field(page, field)
        if not ok:
            field_failures.append(f"{field} ({msg})")

    checkbox_failures = []
    for method in ALT_PAYMENT_METHODS_TO_UNCHECK:
        ok, msg = await uncheck_alt_payment_method(page, method)
        if not ok:
            checkbox_failures.append(f"{method} ({msg})")

    if field_failures or checkbox_failures:
        detail = "; ".join(field_failures + checkbox_failures)[:300]
        print(f"  ✗ [{company_id}] Action 3 — contrôle pré-envoi KO : {detail}")
        await _screenshot_failure(page, company_id, "offer_precheck")
        return "KO", f"contrôle pré-envoi échoué : {detail}"

    # ── Contrôle avant envoi (relecture) ──────────────────────────────────────
    value = await page.locator(SEL_OFFER_NAME_INPUT).input_value()
    if value.strip().lower() != OFFER_VALUE:
        await _screenshot_failure(page, company_id, "offer_precheck")
        return "KO", f"offre '{value}' ≠ '{OFFER_VALUE}' au contrôle final"

    if dry_run:
        print(f"  ⏭ [{company_id}] Action 3 — SIMULÉ (DRY_RUN, offre non envoyée)")
        return "OK", "simulé (dry_run)"

    submit = page.locator(SEL_OFFER_SUBMIT)
    if await submit.count() == 0:
        return "KO", "bouton 'Envoyer l'offre' introuvable"
    await submit.click()
    await asyncio.sleep(2)
    print(f"  ✓ [{company_id}] Action 3 — offre Platinium créée")
    return "OK", "offre créée"


# ─── Orchestration ─────────────────────────────────────────────────────────────
async def process_company(page, company_id: str, dry_run: bool) -> dict:
    result = {"id": company_id}

    for key, label, action in (
        ("action1", "Action 1 (module)",       action1_activate_module),
        ("action2", "Action 2 (feature flags)", action2_feature_flags),
        ("action3", "Action 3 (offre)",         action3_create_offer),
    ):
        try:
            status, message = await action(page, company_id, dry_run)
        except PlaywrightTimeout as e:
            status, message = "KO", f"timeout : {str(e)[:120]}"
            await _screenshot_failure(page, company_id, key)
        except Exception as e:
            status, message = "KO", str(e)[:120]
            await _screenshot_failure(page, company_id, key)

        result[f"{key}_status"]  = status
        result[f"{key}_message"] = message
        print(f"[{company_id}] {label} — {status}{' (' + message + ')' if message else ''}")

    result["overall_status"] = "OK" if all(result[f"action{i}_status"] == "OK" for i in (1, 2, 3)) else "KO"
    return result


async def main():
    os.makedirs("results", exist_ok=True)

    dry_run = get_dry_run()
    print(f"DRY_RUN = {dry_run}\n")

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
            result = await process_company(page, company_id, dry_run)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id",
            "action1_status", "action1_message",
            "action2_status", "action2_message",
            "action3_status", "action3_message",
            "overall_status",
        ])
        writer.writeheader()
        writer.writerows(results)

    ok     = sum(1 for r in results if r["overall_status"] == "OK")
    failed = [r["id"] for r in results if r["overall_status"] != "OK"]
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}/{len(results)}")
    print(f"✗ Échecs  : {len(failed)}")
    if failed:
        print(f"  IDs en échec : {', '.join(failed)}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
