"""
Logique Playwright de ScrapBot — version "web", sans navigateur visible.

Adaptée de admin_payplug/scrapbot.py : prend directement une liste d'IDs (saisis
dans le formulaire, pas de CSV sur le serveur) et les champs à extraire, nécessite
uniquement une session Production (comme l'original — pas de Sandbox/Okta ici).
"""
from __future__ import annotations

import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from bot import try_login

BASE_URL       = "https://admin.payplug.com/admin/companies/{id}"
PROD_LOGIN_URL = "https://admin.payplug.com/admin/login"

SCRAP_TARGETS = {
    "company_ref": {
        "label":    "Company Ref",
        "selector": 'code[data-e2e="companyRef"]',
        "column":   "company_ref",
        "extract":  "text",
    },
    "raison_sociale": {
        "label":    "Raison Sociale",
        "selector": 'input[name="company_name"]',
        "column":   "raison_sociale",
        "extract":  "value",
    },
    "siret": {
        "label":    "SIRET",
        "selector": 'input[name="siret"]',
        "column":   "siret",
        "extract":  "value",
    },
    "nom_commercial": {
        "label":    "Nom Commercial",
        "selector": 'input[name="user_owner[brand_name]"]',
        "column":   "nom_commercial",
        "extract":  "value",
    },
    "user_owner_email": {
        "label":    "User Owner Email",
        "selector": 'p:has(span.label:text-is("Email :"))',
        "column":   "user_owner_email",
        "extract":  "label_text",
    },
    "contact_email": {
        "label":    "Contact Email",
        "selector": 'input[name="contact_email"]',
        "column":   "contact_email",
        "extract":  "value",
    },
    "vat_number": {
        "label":    "N° de TVA intracommunautaire",
        "selector": 'input[name="vat_number"]',
        "column":   "vat_number",
        "extract":  "value",
    },
    "adresse": {
        "label":    "Adresse (siège social)",
        "selector": 'input[name="personal_address"]',
        "column":   "adresse",
        "extract":  "value",
    },
    "code_postal": {
        "label":    "Code Postal",
        "selector": 'input[name="personal_post_code"]',
        "column":   "code_postal",
        "extract":  "value",
    },
    "ville": {
        "label":    "Ville",
        "selector": 'input[name="personal_city"]',
        "column":   "ville",
        "extract":  "value",
    },
    "pays": {
        "label":    "Pays",
        "selector": 'select[name="company_id_country"]',
        "column":   "pays",
        "extract":  "select_text",
    },
    "montant": {
        "label":    "Montant",
        "selector": '[data-e2e="pricing-subscription-amount"]',
        "column":   "montant",
        "extract":  "text",
    },
    "naf_code": {
        "label":    "Code NAF",
        "selector": 'input[name="kyc[naf_code]"]',
        "column":   "naf_code",
        "extract":  "value",
    },
}


async def _extract_value(locator, extract: str) -> str:
    if extract == "value":
        return (await locator.input_value()).strip()
    elif extract == "label_text":
        return await locator.evaluate("el => el.innerText.replace(/^[^:]*:\\s*/, '').trim()")
    elif extract == "select_text":
        return await locator.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")
    else:
        return (await locator.inner_text()).strip()


async def scrape_row(page, url: str, row_id: str, targets: list, log) -> dict:
    result = {"id": row_id, "url": url, "status": "", "message": ""}
    for t in targets:
        result[t["column"]] = ""

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        errors = []
        for t in targets:
            try:
                locator = page.locator(t["selector"]).first
                await locator.wait_for(state="visible", timeout=8000)
                value = await _extract_value(locator, t.get("extract"))
                result[t["column"]] = value
                log(f"  ✓ [{row_id}] {t['column']} → {value}")
            except PlaywrightTimeout:
                errors.append(f"{t['column']} introuvable")
                log(f"  ✗ [{row_id}] {t['column']} introuvable (timeout)")

        if not errors:
            result["status"] = "OK"
        elif len(errors) < len(targets):
            result["status"]  = "PARTIEL"
            result["message"] = " | ".join(errors)
        else:
            result["status"]  = "KO"
            result["message"] = " | ".join(errors)

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:150]
        log(f"✗ [{row_id}] Timeout : {str(e)[:120]}")
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:150]
        log(f"✗ [{row_id}] {str(e)[:120]}")

    return result


async def run_job(
    company_ids: list[str],
    target_keys: list[str],
    prod_storage_state: dict | None,
    credentials: tuple[str, str] | None,
    log,
):
    """Retourne (results, fieldnames). Nécessite soit un session.json Production
    déjà valide (prod_storage_state), soit des identifiants email + mot de passe
    (credentials) pour une connexion automatique — mêmes champs que CompanyStatusBot/
    login.py, sans MFA (pas de navigateur visible ici)."""
    targets = [SCRAP_TARGETS[k] for k in target_keys if k in SCRAP_TARGETS]
    if not targets:
        log("✗ Aucune donnée à scraper sélectionnée — arrêt.")
        return [], []

    if not prod_storage_state and not credentials:
        log("✗ Fournis soit un session.json Production, soit des identifiants email + mot de passe — arrêt.")
        return [], []

    results: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        if prod_storage_state:
            context = await browser.new_context(storage_state=prod_storage_state)
            page    = await context.new_page()
        else:
            context = await browser.new_context()
            page    = await context.new_page()
            try:
                await page.goto(PROD_LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            login, password = credentials
            if not await try_login(page, login, password, log):
                await context.close()
                await browser.close()
                return [], []

        for company_id in company_ids:
            url = BASE_URL.format(id=company_id)
            log(f"→ Scraping {company_id}")
            result = await scrape_row(page, url, company_id, targets, log)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    fieldnames = ["id", "url"] + [t["column"] for t in targets] + ["status", "message"]
    return results, fieldnames
