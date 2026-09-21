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
BASE_URL    = "http://admin.payplug.com/admin/companies/{id}"
TEST_MODE = False

SCRAP_TARGETS = {
    "company_ref": {
        "label":      "Company Ref",
        "selector":   'code[data-e2e="companyRef"]',
        "column":     "company_ref",
        "log_prefix": "company_refs",
        "extract":    "text",
    },
    "raison_sociale": {
        "label":      "Raison Sociale",
        "selector":   'input[name="company_name"]',
        "column":     "raison_sociale",
        "log_prefix": "raison_sociale",
        "extract":    "value",
    },
    "siret": {
        "label":      "SIRET",
        "selector":   'input[name="siret"]',
        "column":     "siret",
        "log_prefix": "siret",
        "extract":    "value",
    },
    "nom_commercial": {
        "label":      "Nom Commercial",
        "selector":   'input[name="user_owner[brand_name]"]',
        "column":     "nom_commercial",
        "log_prefix": "nom_commercial",
        "extract":    "value",
    },
    "user_owner_email": {
        "label":      "User Owner Email",
        "selector":   'p:has(span.label:text-is("Email :"))',
        "column":     "user_owner_email",
        "log_prefix": "user_owner_email",
        "extract":    "label_text",
    },
    "contact_email": {
        "label":      "Contact Email",
        "selector":   'input[name="contact_email"]',
        "column":     "contact_email",
        "log_prefix": "contact_email",
        "extract":    "value",
    },
    "vat_number": {
        "label":      "N° de TVA intracommunautaire",
        "selector":   'input[name="vat_number"]',
        "column":     "vat_number",
        "log_prefix": "vat_number",
        "extract":    "value",
    },
    "adresse": {
        "label":      "Adresse (siège social)",
        "selector":   'input[name="personal_address"]',
        "column":     "adresse",
        "log_prefix": "adresse",
        "extract":    "value",
    },
    "code_postal": {
        "label":      "Code Postal",
        "selector":   'input[name="personal_post_code"]',
        "column":     "code_postal",
        "log_prefix": "code_postal",
        "extract":    "value",
    },
    "ville": {
        "label":      "Ville",
        "selector":   'input[name="personal_city"]',
        "column":     "ville",
        "log_prefix": "ville",
        "extract":    "value",
    },
    "pays": {
        "label":      "Pays",
        "selector":   'select[name="company_id_country"]',
        "column":     "pays",
        "log_prefix": "pays",
        "extract":    "select_text",
    },
    # Ajouter ici de nouveaux types de données à scraper
}
# ───────────────────────────────────────────────────────────────────────────────


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


def ask_targets() -> list:
    """Retourne la liste des clés de targets choisis (sélection multiple).
    Dashboard (checkbox_group) : JSON ["key1", "key2", ...] sur stdin.
    Terminal : numéros séparés par des virgules (ex: 1,3,4)."""
    keys = list(SCRAP_TARGETS.keys())

    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("DONNÉES À SCRAPER (plusieurs choix possibles, séparés par des virgules)")
        print("─────────────────────────────────────────────")
        for i, (key, tdef) in enumerate(SCRAP_TARGETS.items(), 1):
            print(f"  [{i}] {tdef['label']}")
        raw = input("  Votre choix (ex: 1,3,4 — défaut: 1) : ").strip()
        print("─────────────────────────────────────────────\n")
        chosen = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 0 < int(part) <= len(keys):
                chosen.append(keys[int(part) - 1])
        if not chosen:
            chosen = [keys[0]]
        print(f"  → {', '.join(SCRAP_TARGETS[k]['label'] for k in chosen)}")
        return chosen

    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    try:
        chosen = [k for k in json.loads(raw) if k in SCRAP_TARGETS]
    except Exception:
        chosen = []
    if not chosen:
        chosen = [keys[0]]
    print(f"  ✓ Cibles : {', '.join(SCRAP_TARGETS[k]['label'] for k in chosen)}\n")
    return chosen


async def _extract_value(locator, extract: str) -> str:
    if extract == "value":
        return (await locator.input_value()).strip()
    elif extract == "label_text":
        # <p><span class="label">Label :</span> valeur</p> → retire le préfixe "Label :"
        return await locator.evaluate("el => el.innerText.replace(/^[^:]*:\\s*/, '').trim()")
    elif extract == "select_text":
        # <select> disabled : la valeur affichée est le texte de l'option sélectionnée
        return await locator.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")
    else:
        return (await locator.inner_text()).strip()


async def scrape_row(page, url: str, row_id: str, targets: list) -> dict:
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
                print(f"  ✓ [{row_id}] {t['column']} → {value}")
            except PlaywrightTimeout:
                errors.append(f"{t['column']} introuvable")
                print(f"  ✗ [{row_id}] {t['column']} introuvable (timeout)")

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
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] Timeout : {e}")
        await _screenshot_timeout(page, row_id)
    except Exception as e:
        result["status"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{row_id}] {e}")
    return result


async def main():
    os.makedirs("results", exist_ok=True)

    target_keys = ask_targets()
    targets = [SCRAP_TARGETS[k] for k in target_keys]

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    if len(targets) == 1:
        log_file = f"results/{targets[0]['log_prefix']}_{ts}.csv"
    else:
        log_file = f"results/results_scrapbot_multi_{ts}.csv"

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "url" not in df.columns:
        df["url"] = pd.NA
    missing_url = df["url"].isna() | (df["url"].str.strip() == "")
    if missing_url.any():
        if "id" not in df.columns:
            print("⚠ Colonne 'id' requise dans le CSV — arrêt.")
            return
        df.loc[missing_url, "url"] = df.loc[missing_url, "id"].apply(lambda v: BASE_URL.format(id=str(v).strip()))

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
            print(f"\n→ Scraping {row['id']} | {row['url']}")
            result = await scrape_row(page, url=row["url"], row_id=row["id"], targets=targets)
            results.append(result)
            await asyncio.sleep(1)

        await context.close()
        await browser.close()

    fieldnames = ["id", "url"] + [t["column"] for t in targets] + ["status", "message"]
    with open(log_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    ok      = sum(1 for r in results if r["status"] == "OK")
    partiel = sum(1 for r in results if r["status"] == "PARTIEL")
    err     = len(results) - ok - partiel
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    if partiel:
        print(f"⚠ Partiels : {partiel}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
