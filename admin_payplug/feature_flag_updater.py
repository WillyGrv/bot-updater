import asyncio
import csv
import json
import sys
from glob import glob
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from datetime import datetime

LOG_FILE    = f"results/results_feature_flags_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = True

BASE_URL    = "https://admin.payplug.com/companies/{company_ref}/feature-flags"
FLAG_INPUT  = '#new-flag-name-input'
FLAG_SUBMIT = 'button[data-e2e="new-feature-flag-submit"]'


async def _screenshot_timeout(page, identifier: str) -> None:
    import os
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


def get_company_refs_files() -> list[str]:
    return sorted(glob("results/company_refs_*.csv"), reverse=True)


def ask_config() -> tuple[str, str, str]:
    """
    Retourne (flag_name, source_mode, company_refs_filename).
    source_mode : "csv_file" | "input"
    - Terminal (TTY) : prompts interactifs
    - Dashboard/pipe : ligne 1 = flag_name, ligne 2 = source_mode, ligne 3 = filename
    """
    files = get_company_refs_files()

    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        flag_name = input("  Nom du feature flag à créer : ").strip()
        if not flag_name:
            return "", "", ""
        print("\n  Source des company_refs :")
        print("    [0] Fichier company_refs (résultats ScrapBot)")
        print("    [1] Input - Company_ref (channel_accounts.csv)")
        src_choice = input("  Choix [0] : ").strip()
        if src_choice == "1":
            print("─────────────────────────────────────────────\n")
            return flag_name, "input", ""
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
        return flag_name, "csv_file", selected

    try:
        flag_name   = input().strip()
        source_mode = input().strip() or "csv_file"
        selected    = input().strip()
        if source_mode == "csv_file" and not selected and files:
            selected = files[0]
        print(f"  ✓ Flag    : {flag_name}")
        print(f"  ✓ Source  : {source_mode}")
        if source_mode == "csv_file":
            print(f"  ✓ Fichier : {selected}\n")
        else:
            print(f"  ✓ Fichier : input/channel_accounts.csv\n")
        return flag_name, source_mode, selected
    except EOFError:
        return "", "", ""


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
        await _screenshot_timeout(page, row_id)
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")
    return result


async def main():
    import os; os.makedirs("results", exist_ok=True)
    flag_name, source_mode, company_refs_file = ask_config()
    if not flag_name:
        print("Nom du flag vide — arrêt.")
        return

    rows = []
    if source_mode == "input":
        src_path = "input/channel_accounts.csv"
        print(f"Lecture de {src_path}...")
        with open(src_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row = {k.strip().lower(): v for k, v in row.items()}
                if row.get("company_ref"):
                    if "id" not in row:
                        row["id"] = row["company_ref"]
                    rows.append(row)
    else:
        if not company_refs_file:
            print("Aucun fichier company_refs trouvé — lance d'abord 'Scraper les Realm IDs'.")
            return
        print(f"Lecture de {company_refs_file}...")
        with open(company_refs_file, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("company_ref") and row.get("status", "").upper() == "OK":
                    rows.append(row)

    if not rows:
        print("Aucune ligne valide avec company_ref — arrêt.")
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
