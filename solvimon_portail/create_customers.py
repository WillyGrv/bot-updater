import asyncio
import csv
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime
from base import login_and_get_page

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE   = "input/customers_input.csv"
LOG_FILE      = f"results/results_customers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE     = True

CUSTOMERS_URL = "https://desk.solvimon.com/customers"
# ──────────────────────────────────────────────────────────────────────────────
#
# Mapping CSV → champs du formulaire Solvimon :
#   LEGAL NAME         → input[name="org-legal-name"]
#   REFERENCE          → get_by_label("Reference")  (name UUID dynamique)
#   COUNTRY            → input[name="org-address-country"]  combobox
#   LOCALE             → input[name="org-locale"]           combobox
#   EMAIL ADDRESS      → input[name="org-email"]
#   REGISTRATION NUMBER→ input[name="org-registration-number"]
#   TAX-ID             → input[name="org-tax-id"]
#   ADDRESS LINE1      → input[name="org-address-line-1"]
#   ADDRESS LINE2      → input[name="org-address-line-2"]
#   CITY               → input[name="org-address-city"]
#   STATE              → input[name="org-address-state"]
#   POSTAL CODE        → input[name="org-address-postal-code"]
# ──────────────────────────────────────────────────────────────────────────────


def is_empty(val):
    return val is None or str(val).strip() in ("", "nan", "NaN")


async def _screenshot_timeout(page, identifier):
    os.makedirs("screenshots", exist_ok=True)
    try:
        path_png = f"screenshots/timeout_{identifier}.png"
        await page.screenshot(path=path_png, full_page=True)
        print(f"  📸 Screenshot → {path_png}")
        html = await page.evaluate("() => document.body ? document.body.innerHTML.slice(0, 3000) : ''")
        with open(f"screenshots/timeout_{identifier}.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"  ⚠ Capture debug échouée : {e}")


async def fill_text(page, name_attr, value):
    """Remplit un input[name] simple."""
    if is_empty(value):
        return
    await page.locator(f'input[name="{name_attr}"]').fill(str(value).strip(), timeout=5000)


async def fill_by_label(page, label_text, value):
    """Remplit un input UUID-dynamique : lit le 'for' du label pour cibler l'input par id."""
    if is_empty(value):
        return
    label  = page.locator("label").filter(has_text=label_text).first
    for_id = await label.get_attribute("for", timeout=5000)
    inp    = page.locator(f"#{for_id}")
    await inp.click(click_count=3, timeout=5000)
    await inp.press("Control+a")
    await inp.fill(str(value).strip(), timeout=5000)


async def fill_combobox(page, name_attr, value):
    """Tape dans un combobox (via input[name]) et sélectionne la 1ère option de la liste."""
    if is_empty(value):
        return
    value = str(value).strip()
    inp = page.locator(f'input[name="{name_attr}"]')
    await inp.click(timeout=5000)
    await inp.fill(value, timeout=5000)
    await asyncio.sleep(1)
    option = page.locator('[role="option"]').first
    try:
        await option.wait_for(state="visible", timeout=6000)
        await option.click()
        await asyncio.sleep(0.5)
    except PlaywrightTimeout:
        print(f"  ⚠ Aucune option pour « {value} » (name: {name_attr})")


async def fill_combobox_country(page, country_code):
    """Combobox country : cible l'option exacte via data-testid='checkbox-item-{CODE}'."""
    if is_empty(country_code):
        return
    code = str(country_code).strip().upper()
    inp  = page.locator('input[name="org-address-country"]')
    await inp.click(timeout=5000)
    await inp.fill(code, timeout=5000)
    await asyncio.sleep(1)
    option = page.locator(f'[data-testid="checkbox-item-{code}"]')
    try:
        await option.wait_for(state="visible", timeout=6000)
        await option.click()
        await asyncio.sleep(0.5)
    except PlaywrightTimeout:
        print(f"  ⚠ Option pays introuvable pour le code « {code} »")


async def create_customer(page, row, row_id):
    result = {
        "id":          row_id,
        "legal_name":  str(row.get("legal name", "") or ""),
        "reference":   str(row.get("reference", "") or ""),
        "customer_id": "",
        "status":      "",
        "message":     "",
    }

    try:
        # ── 1. Page customers ───────────────────────────────────────────────
        await page.goto(CUSTOMERS_URL, wait_until="networkidle", timeout=20000)
        await asyncio.sleep(1.5)

        # ── 2. Ouvrir le formulaire ─────────────────────────────────────────
        await page.locator('button:has-text("Add customer")').first.click(timeout=8000)
        await asyncio.sleep(2)

        # ── 3. Remplir les champs ───────────────────────────────────────────
        await fill_text(page,           "org-legal-name",          row.get("legal name"))
        await fill_combobox_country(page,                          row.get("country"))
        await fill_combobox(page,       "org-locale",              row.get("locale"))
        await fill_text(page,           "org-email",               row.get("email address"))
        await fill_text(page,           "org-registration-number", row.get("registration number"))
        await fill_text(page,           "org-tax-id",              row.get("tax-id"))
        await fill_text(page,           "org-address-line-1",      row.get("address line1"))
        await fill_text(page,           "org-address-line-2",      row.get("address line2"))
        await fill_text(page,           "org-address-city",        row.get("city"))
        await fill_text(page,           "org-address-state",       row.get("state"))
        await fill_text(page,           "org-address-postal-code", row.get("postal code"))
        # Reference en dernier — attend que l'auto-génération Solvimon soit terminée
        await asyncio.sleep(1.5)
        await fill_by_label(page,  "Reference",                    row.get("reference"))

        await asyncio.sleep(0.5)

        # ── 4. Sauvegarder ─────────────────────────────────────────────────
        if TEST_MODE:
            await page.locator('button[type="submit"]:has-text("Save as draft")').click(timeout=8000)
        else:
            await page.locator('[data-testid="submit-customer-button"]').click(timeout=8000)

        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(2)

        # ── 5. Récupérer l'ID depuis l'URL ──────────────────────────────────
        current_url = page.url
        cust_id = current_url.split("/customers/")[-1].split("/")[0] if "/customers/" in current_url else ""
        result["customer_id"] = cust_id
        result["status"]      = "OK"
        print(f"  ✓ [{row_id}] Customer créé → {cust_id or '(ID non trouvé)'}")

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

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()
    df = df.fillna("")

    if TEST_MODE:
        df = df.head(1)
        print("[MODE TEST] 1 seule ligne — Save as draft.\n")
    else:
        print(f"[PROD] {len(df)} lignes — Save and activate.\n")

    results = []

    async with async_playwright() as p:
        browser, context, page = await login_and_get_page(p)

        for i, (_, row) in enumerate(df.iterrows(), 1):
            name = str(row.get("legal name", "")).strip() or f"ligne_{i}"
            print(f"\n→ [{i}] {name}")
            result = await create_customer(page, row, str(i))
            results.append(result)
            await asyncio.sleep(2)

        await context.close()
        await browser.close()

    fieldnames = ["id", "legal_name", "reference", "customer_id", "status", "message"]
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
