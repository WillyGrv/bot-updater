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
EDIT_BUTTON = "#change"
SAVE_BUTTON = "input[type='submit'].main-submit"

SEL_COMMENT_TEXTAREA  = "#new-comment"
SEL_COMMENT_SUBMIT    = "form#add-comment input[type='submit']"
SEL_COMMENT_LOG_LIST  = "#all-logs"
SEL_COMMENT_FIRST_PIN = "#all-logs .company-log:first-child i.pin"
LOG_FILE    = f"results/results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = True

# ── Champs disponibles ─────────────────────────────────────────────────────────
# value_mode: "fixed"   → même valeur pour toutes les lignes (saisie au démarrage)
#             "dynamic" → valeur lue depuis une colonne du CSV d'entrée
AVAILABLE_FIELDS = {
    "contact_email": {
        "label":      "Email de contact",
        "selector":   "input[name='contact_email']",
        "field_type": "text",
        "value_mode": "fixed",
        "csv_column": None,
    },
    "master_company_id": {
        "label":      "Compte Master",
        "selector":   "input[name='master_company_id']",
        "field_type": "text",
        "value_mode": "fixed",
        "csv_column": None,
    },
    "commentaire": {
        "label":      "Commentaire",
        "selector":   None,
        "field_type": "comment",
        "value_mode": "fixed",
        "csv_column": None,
    },
    "pin_commentaire": {
        "label":      "Épingler le commentaire",
        "selector":   None,
        "field_type": "select",
        "value_mode": "fixed",
        "csv_column": None,
        "options": [
            {"value": "non", "label": "Non"},
            {"value": "oui", "label": "Oui"},
        ],
    },
    "siret": {
        "label":      "SIRET",
        "selector":   "input[name='siret']",
        "field_type": "text",
        "value_mode": "dynamic",
        "csv_column": "siret",
    },
    "type": {
        "label":      "Type",
        "selector":   "select[name='type']",
        "field_type": "select",
        "value_mode": "fixed",
        "csv_column": None,
        "options": [
            {"value": "-50", "label": "Test (en prod)"},
            {"value": "0",   "label": "Default"},
            {"value": "50",  "label": "Marchand GM"},
        ],
    },
    "account_manager": {
        "label":      "Account Manager",
        "selector":   "select[name='id_account_manager']",
        "field_type": "select",
        "value_mode": "fixed",
        "csv_column": None,
        "options": [
            {"value": "",      "label": "Aucun"},
            {"value": "-1",    "label": "Pas d'account manager"},
            {"value": "110",   "label": "Audrey Alia"},
            {"value": "160",   "label": "Audrey Galy"},
            {"value": "18",    "label": "Antoine Grimaud"},
            {"value": "10388", "label": "Anaïs Nesse"},
            {"value": "10594", "label": "antoine Rousseau"},
            {"value": "10323", "label": "Antoine Raynaud"},
            {"value": "20651", "label": "Arthur Robert_payplug"},
            {"value": "161",   "label": "Alessandro Ursini"},
            {"value": "10255", "label": "Chiara Chaignaud"},
            {"value": "10280", "label": "Christelle Mentor"},
            {"value": "20692", "label": "Clément Willk-Fabia"},
            {"value": "30",    "label": "Eric Cohen"},
            {"value": "10553", "label": "Erwan Dronne"},
            {"value": "35",    "label": "François Bureau"},
            {"value": "10589", "label": "Fannie Lauze"},
            {"value": "100",   "label": "Federica Narbone"},
            {"value": "10550", "label": "Gaetan Coatleven"},
            {"value": "20721", "label": "Gautier Toulemonde"},
            {"value": "10199", "label": "Irène de Giorgio"},
            {"value": "10324", "label": "Ilyes Djebnoune"},
            {"value": "10225", "label": "Juliette Manyères"},
            {"value": "10595", "label": "Jonathan Mayamona"},
            {"value": "10226", "label": "Kenza Guerinat"},
            {"value": "124",   "label": "Ludovica Durelli"},
            {"value": "10244", "label": "Marie Bruguera"},
            {"value": "127",   "label": "Martina Foggiano"},
            {"value": "20631", "label": "Matthew Houtart"},
            {"value": "10327", "label": "Michelangelo Palumbo"},
            {"value": "67",    "label": "Marie-Rebecca El Hachem"},
            {"value": "10486", "label": "Nathan Duc"},
            {"value": "10556", "label": "Oceane Schenker"},
            {"value": "10590", "label": "Pedro Rodrigues"},
            {"value": "10434", "label": "Romane Jarillon"},
            {"value": "20661", "label": "Romain Pastureau"},
            {"value": "10309", "label": "Ronan Ponce"},
            {"value": "10592", "label": "Ulysse Hottier"},
            {"value": "125",   "label": "Xavier Lespine"},
        ],
    },
}
# ───────────────────────────────────────────────────────────────────────────────


def _build_ops(selection: dict) -> list:
    """Convertit {field_id: value} en liste d'opérations."""
    ops = []
    for field_id, value in selection.items():
        if field_id not in AVAILABLE_FIELDS:
            continue
        fdef = AVAILABLE_FIELDS[field_id]
        ops.append({
            "field_id":   field_id,
            "selector":   fdef["selector"],
            "field_type": fdef.get("field_type", "text"),
            "value":      value,
            "mode":       fdef["value_mode"],
            "csv_column": fdef.get("csv_column"),
        })
    return ops


def _ask_interactive() -> list:
    """Mode terminal : demande interactive pour chaque champ disponible."""
    ops = []
    print("─────────────────────────────────────────────")
    print("CHAMPS À MODIFIER")
    print("─────────────────────────────────────────────")
    for field_id, fdef in AVAILABLE_FIELDS.items():
        sel = input(f"  Modifier '{fdef['label']}' ? [o/N] : ").strip().lower()
        if sel == 'o':
            if fdef['value_mode'] == 'fixed':
                if fdef.get('field_type') == 'select':
                    for opt in fdef.get('options', []):
                        print(f"    {opt['value']:>8}  {opt['label']}")
                    val = input(f"  → Value à appliquer : ").strip()
                else:
                    val = input(f"  → Valeur à appliquer : ").strip()
                ops.append({
                    "field_id":   field_id,
                    "selector":   fdef["selector"],
                    "field_type": fdef.get("field_type", "text"),
                    "value":      val,
                    "mode":       "fixed",
                    "csv_column": None,
                })
            else:
                ops.append({
                    "field_id":   field_id,
                    "selector":   fdef["selector"],
                    "field_type": fdef.get("field_type", "text"),
                    "value":      "__csv__",
                    "mode":       "dynamic",
                    "csv_column": fdef["csv_column"],
                })
    print("─────────────────────────────────────────────\n")
    return ops


def ask_config() -> list:
    """
    Retourne la liste des opérations à effectuer.
    - Terminal (TTY) : prompts interactifs
    - Dashboard/pipe : lit une ligne JSON {"field_id": "value", ...}
    """
    if sys.stdin.isatty():
        return _ask_interactive()
    try:
        raw = input().strip()
        selection = json.loads(raw)
        ops = _build_ops(selection)
        for op in ops:
            label = AVAILABLE_FIELDS[op["field_id"]]["label"]
            val   = op["value"] if op["mode"] == "fixed" else f"[CSV:{op['csv_column']}]"
            print(f"  ✓ {label} : {val}")
        print()
        return ops
    except (EOFError, json.JSONDecodeError) as e:
        print(f"⚠ Configuration invalide ({e}) — aucun champ sélectionné.\n")
        return []


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


COMMENT_FIELD_IDS = {"commentaire", "pin_commentaire"}


async def update_page(page, url: str, row_id: str, field_ops: list, row_data: dict) -> dict:
    result = {"id": row_id, "url": url, "status": "", "message": ""}

    for op in field_ops:
        value = op["value"] if op["mode"] == "fixed" else row_data.get(op["csv_column"], "")
        result[op["field_id"]] = value

    edit_ops   = [op for op in field_ops if op["field_id"] not in COMMENT_FIELD_IDS]
    comment_op = next((op for op in field_ops if op["field_id"] == "commentaire"), None)
    pin_op     = next((op for op in field_ops if op["field_id"] == "pin_commentaire"), None)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        # ── Champs standard (formulaire edit) ─────────────────────────────────
        if edit_ops:
            await page.wait_for_selector(EDIT_BUTTON, timeout=8000)
            await page.click(EDIT_BUTTON)

            save_edit = SAVE_BUTTON + ":not([readonly])"
            await page.wait_for_selector(save_edit, timeout=8000)

            for op in edit_ops:
                value = op["value"] if op["mode"] == "fixed" else row_data.get(op["csv_column"], "")
                await page.wait_for_selector(op["selector"], timeout=8000)
                if op.get("field_type") == "select":
                    print(f"  → Tentative select : selector='{op['selector']}' value='{value}'")
                    locator = page.locator(op["selector"])
                    await locator.wait_for(state="visible", timeout=8000)
                    await locator.select_option(value=str(value))
                    confirmed = await page.eval_on_selector(op["selector"], "el => el.value")
                    print(f"  → Valeur confirmée dans le DOM : '{confirmed}'")
                else:
                    await page.fill(op["selector"], "")
                    await page.fill(op["selector"], str(value))
                print(f"  ✓ [{row_id}] {AVAILABLE_FIELDS[op['field_id']]['label']} → {value}")

            await page.click(save_edit)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)

        # ── Commentaire (soumission AJAX — pas de rechargement de page) ─────────
        if comment_op:
            comment_text = comment_op["value"] if comment_op["mode"] == "fixed" else row_data.get(comment_op["csv_column"], "")
            pin = False
            if pin_op:
                pin_val = pin_op["value"] if pin_op["mode"] == "fixed" else row_data.get(pin_op["csv_column"], "")
                pin = str(pin_val).lower() in ("oui", "o", "1", "true")

            await page.wait_for_selector(SEL_COMMENT_TEXTAREA, timeout=8000)
            await page.fill(SEL_COMMENT_TEXTAREA, comment_text)

            # Capturer l'ID du premier log avant soumission pour détecter l'ajout
            first_id_before = await page.evaluate("""
                () => {
                    const el = document.querySelector('#all-logs .company-log');
                    return el ? el.getAttribute('id-company-log') : null;
                }
            """)

            await page.click(SEL_COMMENT_SUBMIT)

            # Attendre que le nouveau log apparaisse en tête de #all-logs (réponse AJAX)
            await page.wait_for_function(
                """(prevId) => {
                    const first = document.querySelector('#all-logs .company-log');
                    return first && first.getAttribute('id-company-log') !== prevId;
                }""",
                arg=first_id_before,
                timeout=8000,
            )
            print(f"  ✓ [{row_id}] Commentaire ajouté")

            if pin:
                # i.pin est visibility:hidden — on déclenche le click jQuery via JS
                pinned = await page.evaluate("""
                    () => {
                        const first = document.querySelector('#all-logs .company-log');
                        if (!first) return false;
                        const pin = first.querySelector('i.pin');
                        if (!pin) return false;
                        pin.click();
                        return true;
                    }
                """)
                await asyncio.sleep(1.5)  # laisser l'AJAX de pin se terminer
                if pinned:
                    print(f"  ✓ [{row_id}] Commentaire épinglé")
                else:
                    print(f"  ⚠ [{row_id}] Pin non trouvé")

        result["status"] = "OK"

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
    field_ops = ask_config()
    if not field_ops:
        print("Aucun champ sélectionné — arrêt.")
        return

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if TEST_MODE:
        df = df.head(1)
        print(f"[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} lignes à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            row_data = row.to_dict()
            print(f"\n→ Traitement {row['id']} | {row['url']}")
            result = await update_page(
                page,
                url=row["url"],
                row_id=row["id"],
                field_ops=field_ops,
                row_data=row_data,
            )
            results.append(result)
            await asyncio.sleep(1.5)

        await context.close()
        await browser.close()

    field_columns = [op["field_id"] for op in field_ops]
    fieldnames    = ["id", "url"] + field_columns + ["status", "message"]

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
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
