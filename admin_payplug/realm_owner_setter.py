import asyncio
import csv
import json
import os
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
DATA_SOURCE = "input/realm_owners.csv"
BASE_URL    = "https://admin.payplug.com/realm-companies?realm_ref={realm_id}"
LOG_FILE    = f"results/results_realm_owners_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False
MAX_PAGES = 20

SEL_SEARCH_INPUT    = 'input[placeholder*="Rechercher une entreprise"]'
SEL_COMPANIES_TABLE = "realm-companies-table"
SEL_COMPANY_REF     = "realm-company-ref"
SEL_CHANGE_OWNER_BTN = 'button[data-e2e="change-owner-button"]'

SEL_USERS_TABLE  = "realm-users-selection-table"
SEL_USER_EMAIL   = "change-owner-realm-user-email"
SEL_USER_RADIO   = 'input[data-e2e="change-owner-select-user"]'
SEL_CONFIRM_BTN  = 'button[data-e2e="change-owner-modal-confirm"]'
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


async def set_page_size_100(page, container) -> None:
    """Passe la pagination du tableau à 100 éléments / page pour limiter le nombre de pages à parcourir."""
    try:
        size_changer = page.locator(".ant-pagination-options-size-changer").last
        if await size_changer.count() == 0:
            print("  ⚠ Sélecteur 'par page' introuvable — pagination 10/page conservée")
            return

        current = (await size_changer.locator(".ant-select-content").text_content() or "").strip()
        if current == "100":
            return

        await size_changer.click()

        dropdown_option = page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(has_text="100")
        await dropdown_option.first.wait_for(state="visible", timeout=5000)
        await dropdown_option.last.click()
        await asyncio.sleep(1)
        print("  ℹ Pagination passée à 100 / page")

    except Exception as e:
        print(f"  ⚠ Impossible de passer à 100 / page : {str(e)[:80]}")


async def find_row_by_cell_text(page, table_data_e2e: str, cell_data_e2e: str, target_text: str, max_pages: int = MAX_PAGES):
    """Parcourt les pages d'un tableau ant-design et retourne la ligne dont la cellule correspond exactement au texte cherché."""
    container = page.locator(f'.ant-spin-container:has(div[data-e2e="{table_data_e2e}"])')
    target = target_text.strip().lower()

    await page.locator(f'div[data-e2e="{table_data_e2e}"]').wait_for(state="visible", timeout=20000)

    # Afficher 100 lignes / page pour réduire le nombre de pages à parcourir
    await set_page_size_100(page, container)

    # Repartir de la page 1 (la pagination peut être restée sur une page précédente)
    page1_item = container.locator("li.ant-pagination-item-1")
    if await page1_item.count() > 0:
        cls = await page1_item.first.get_attribute("class") or ""
        if "ant-pagination-item-active" not in cls:
            await page1_item.first.locator("button, a").click()
            await asyncio.sleep(1)

    for _ in range(max_pages):
        await container.locator("table tbody tr").first.wait_for(state="visible", timeout=20000)
        rows  = container.locator("table tbody tr")
        count = await rows.count()

        for i in range(count):
            row  = rows.nth(i)
            cell = row.locator(f'[data-e2e="{cell_data_e2e}"]')
            if await cell.count() == 0:
                continue
            text = (await cell.first.text_content() or "").strip().lower()
            if text == target:
                return row

        next_btn = container.locator("li.ant-pagination-next")
        if await next_btn.count() == 0:
            break
        if await next_btn.get_attribute("aria-disabled") == "true":
            break
        await next_btn.click()
        await asyncio.sleep(1)

    return None


async def set_owner(page, realm_id: str, company_ref: str, email: str, url: str) -> dict:
    result = {"realm_id": realm_id, "company_ref": company_ref, "email": email, "status": "", "message": ""}

    try:
        # Page rechargée à chaque ligne pour repartir d'un état propre (page 1, pas de filtre résiduel)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        search_input = page.locator(SEL_SEARCH_INPUT)
        await search_input.wait_for(state="visible", timeout=20000)
        await search_input.fill(company_ref)
        await asyncio.sleep(1.5)

        company_row = await find_row_by_cell_text(page, SEL_COMPANIES_TABLE, SEL_COMPANY_REF, company_ref, max_pages=3)
        if company_row is None:
            result["status"]  = "ERREUR"
            result["message"] = "company_ref introuvable"
            print(f"  ✗ [{company_ref}] introuvable dans le tableau")
            await _screenshot_timeout(page, f"{company_ref}_notfound")
            return result

        await company_row.locator(SEL_CHANGE_OWNER_BTN).click()
        await asyncio.sleep(1)

        await page.locator(f'div[data-e2e="{SEL_USERS_TABLE}"]').wait_for(state="visible", timeout=20000)

        user_row = await find_row_by_cell_text(page, SEL_USERS_TABLE, SEL_USER_EMAIL, email)
        if user_row is None:
            result["status"]  = "ERREUR"
            result["message"] = "email introuvable dans la liste des utilisateurs"
            print(f"  ✗ [{company_ref}] email '{email}' introuvable")
            await _screenshot_timeout(page, f"{company_ref}_{email}_notfound")
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
            return result

        await user_row.locator(SEL_USER_RADIO).click()
        await asyncio.sleep(0.3)

        await page.locator(SEL_CONFIRM_BTN).click()
        await asyncio.sleep(1.5)

        result["status"] = "OK"
        print(f"  ✓ [{company_ref}] propriétaire changé → {email}")

    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_ref}] Timeout : {str(e)[:80]}")
        await _screenshot_timeout(page, f"{company_ref}_{email}")

    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_ref}] {str(e)[:80]}")

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

    if "company_ref" not in df.columns or "email" not in df.columns:
        print("⚠ Colonnes 'company_ref' et 'email' requises dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["company_ref", "email"])

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} ligne(s) à traiter.\n")

    results = []
    url = BASE_URL.format(realm_id=realm_id)

    log_f  = open(LOG_FILE, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(log_f, fieldnames=["realm_id", "company_ref", "email", "status", "message"])
    writer.writeheader()
    log_f.flush()

    def log_result(result: dict) -> None:
        results.append(result)
        writer.writerow(result)
        log_f.flush()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        try:
            print(f"→ Royaume {realm_id}")

            for _, row in df.iterrows():
                company_ref = str(row["company_ref"]).strip()
                email       = str(row["email"]).strip()
                print(f"\n→ {company_ref} → {email}")
                result = await set_owner(page, realm_id, company_ref, email, url)
                log_result(result)
                await asyncio.sleep(1.0)

        except PlaywrightTimeout as e:
            print(f"  ✗ [{realm_id}] Erreur d'ouverture — Timeout : {str(e)[:80]}")
            await _screenshot_timeout(page, realm_id)
            for _, row in df.iterrows():
                if any(r["company_ref"] == str(row["company_ref"]).strip() and r["email"] == str(row["email"]).strip() for r in results):
                    continue
                log_result({
                    "realm_id":    realm_id,
                    "company_ref": str(row["company_ref"]).strip(),
                    "email":       str(row["email"]).strip(),
                    "status":      "ERREUR_TIMEOUT",
                    "message":     str(e)[:120],
                })

        except Exception as e:
            print(f"  ✗ [{realm_id}] Erreur d'ouverture — {str(e)[:80]}")
            for _, row in df.iterrows():
                if any(r["company_ref"] == str(row["company_ref"]).strip() and r["email"] == str(row["email"]).strip() for r in results):
                    continue
                log_result({
                    "realm_id":    realm_id,
                    "company_ref": str(row["company_ref"]).strip(),
                    "email":       str(row["email"]).strip(),
                    "status":      "ERREUR",
                    "message":     str(e)[:120],
                })

        await context.close()
        await browser.close()

    log_f.close()

    ok  = sum(1 for r in results if r["status"] == "OK")
    err = sum(1 for r in results if r["status"] != "OK")
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
