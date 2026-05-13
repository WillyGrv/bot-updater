import asyncio
import csv
import json
import sys
from glob import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

LOG_FILE    = f"results_feature_flags_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE   = False

BASE_URL    = "https://admin.payplug.com/companies/{company_ref}/feature-flags"
FLAG_INPUT  = '#new-flag-name-input'
FLAG_SUBMIT = 'button[data-e2e="new-feature-flag-submit"]'


def load_session(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    for origin in session.get("origins", []):
        for item in origin.get("localStorage", []):
            if not isinstance(item.get("value"), str):
                item["value"] = json.dumps(item["value"])
    return session


def get_company_refs_files() -> list[str]:
    return sorted(glob("company_refs_*.csv"), reverse=True)


def ask_config() -> tuple[str, str]:
    """
    Retourne (flag_name, company_refs_filename).
    - Terminal (TTY) : prompts interactifs
    - Dashboard/pipe : lit ligne 1 = flag_name, ligne 2 = filename (vide = dernier)
    """
    files = get_company_refs_files()

    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        flag_name = input("  Nom du feature flag à créer : ").strip()
        if not flag_name:
            return "", ""
        if files:
            print(f"\n  Fichiers company_refs disponibles :")
            for i, f in enumerate(files):
                marker = " ← dernier" if i == 0 else ""
                print(f"    [{i}] {f}{marker}")
            choice = input(f"\n  Numéro du fichier [Entrée = dernier] : ").strip()
            if choice.isdigit() and int(choice) < len(files):
                selected = files[int(choice)]
            else:
                selected = files[0]
        else:
            selected = ""
        print("─────────────────────────────────────────────\n")
        return flag_name, selected

    try:
        flag_name = input().strip()
        selected  = input().strip()
        if not selected and files:
            selected = files[0]
        print(f"  ✓ Flag    : {flag_name}")
        print(f"  ✓ Fichier : {selected}\n")
        return flag_name, selected
    except EOFError:
        return "", ""


async def apply_feature_flag(page, company_ref: str, row_id: str, flag_name: str) -> dict:
    url    = BASE_URL.format(company_ref=company_ref)
    result = {"id": row_id, "company_ref": company_ref, "flag_name": flag_name, "url": url,
              "status": "", "message": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(FLAG_INPUT, timeout=8000)
        await page.fill(FLAG_INPUT, "")
        await page.fill(FLAG_INPUT, flag_name)
        await page.click(FLAG_SUBMIT)
        await page.wait_for_timeout(2000)
        result["status"] = "OK"
        print(f"  ✓ [{row_id}] {flag_name} → {company_ref}")
    except PlaywrightTimeout as e:
        result["status"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {e}")
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")
    return result


async def main():
    flag_name, company_refs_file = ask_config()
    if not flag_name:
        print("Nom du flag vide — arrêt.")
        return
    if not company_refs_file:
        print("Aucun fichier company_refs trouvé — lance d'abord 'Scraper les Realm IDs'.")
        return

    print(f"Lecture de {company_refs_file}...")
    rows = []
    with open(company_refs_file, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("company_ref") and row.get("status", "").upper() == "OK":
                rows.append(row)

    if not rows:
        print("Aucune ligne valide (company_ref + status OK) — arrêt.")
        return

    if TEST_MODE:
        rows = rows[:1]
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(rows)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for row in rows:
            print(f"\n→ Traitement {row['id']} | company_ref={row['company_ref']}")
            result = await apply_feature_flag(
                page,
                company_ref=row["company_ref"],
                row_id=row["id"],
                flag_name=flag_name,
            )
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "company_ref", "flag_name", "url", "status", "message"]
        )
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
