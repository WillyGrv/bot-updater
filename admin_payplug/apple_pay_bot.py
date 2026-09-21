"""
ApplePayBot
───────────
Pour chaque ID de input/data.csv, sur https://admin.payplug.com/admin/companies/{id} :
  1. Clique sur "Onboarding Apple Pay".
  2. Attend 3s le chargement de l'écran.
  3. Renseigne le domaine à déclarer et clique sur "Envoyer".
  4. Vérifie que le domaine apparaît bien dans la liste des domaines approuvés.

Lancement (terminal) :
    python apple_pay_bot.py
Ou depuis le dashboard (bouton "ApplePayBot" du bot Admin PayPlug).

DRY_RUN : saisit le domaine mais ne clique pas sur "Envoyer".
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
LOG_FILE    = f"results/results_apple_pay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

COMPANY_URL = "https://admin.payplug.com/admin/companies/{id}"

SEL_LINK_APPLE_PAY   = 'a[data-e2e="apple-pay"]'
SEL_DOMAIN_INPUT     = 'input[data-e2e="new-domain-name-input-field-1"]'
SEL_SUBMIT           = 'button[data-e2e="submit-new-domain-names"]'
SEL_APPROVED_DOMAIN  = 'p[data-e2e="approved-domain-name"]'
# ──────────────────────────────────────────────────────────────────────────────


def get_config() -> tuple[str, bool]:
    """Priorité DRY_RUN : variable d'env, sinon paramètre dashboard (stdin), sinon prompt manuel."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("CONFIGURATION")
        print("─────────────────────────────────────────────")
        domain  = input("  Domaine à déclarer (ex: secure.payplug.com) : ").strip()
        dry_raw = input("  DRY_RUN ? (o/N) : ").strip().lower()
        print("─────────────────────────────────────────────\n")
    else:
        try:
            domain  = input().strip()
            dry_raw = input().strip().lower()
        except EOFError:
            print("⚠ Paramètres manquants.")
            sys.exit(1)

    if not domain:
        print("⚠ Domaine vide — arrêt.")
        sys.exit(1)

    env_val = os.environ.get("DRY_RUN", "").strip().lower()
    if env_val in ("1", "true", "yes", "oui"):
        dry_run = True
    elif env_val in ("0", "false", "no", "non"):
        dry_run = False
    else:
        dry_run = dry_raw in ("o", "oui", "y", "yes", "1", "true")

    print(f"  Domaine : {domain}")
    print(f"  DRY_RUN : {dry_run}\n")
    return domain, dry_run


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


async def process_company(page, company_id: str, domain: str, dry_run: bool) -> dict:
    result = {"id": company_id, "domain": domain, "status": "", "message": ""}

    try:
        await page.goto(COMPANY_URL.format(id=company_id), wait_until="domcontentloaded", timeout=15000)

        link = page.locator(SEL_LINK_APPLE_PAY)
        if await link.count() == 0:
            result.update(status="KO", message="lien 'Onboarding Apple Pay' introuvable")
            return result
        await link.click()
        await asyncio.sleep(3)

        domain_input = page.locator(SEL_DOMAIN_INPUT)
        try:
            await domain_input.wait_for(state="visible", timeout=10000)
        except PlaywrightTimeout:
            await _screenshot_failure(page, company_id, "domain_input")
            result.update(status="KO", message="champ domaine introuvable (page non chargée après 10s)")
            return result

        await domain_input.fill(domain)
        actual = (await domain_input.input_value()).strip()
        if actual != domain:
            result.update(status="KO", message=f"valeur '{actual}' ≠ '{domain}' attendue après saisie")
            return result

        if dry_run:
            print(f"  ⏭ [{company_id}] SIMULÉ (DRY_RUN) — domaine '{domain}' saisi, non envoyé")
            result.update(status="OK", message="simulé (dry_run)")
            return result

        submit = page.locator(SEL_SUBMIT)
        try:
            await submit.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeout:
            pass
        # Le bouton est désactivé tant que le champ est vide — laisse le temps à l'appli
        # de le débloquer après la saisie avant de vérifier.
        if await submit.is_disabled():
            await asyncio.sleep(0.5)
        if await submit.is_disabled():
            await _screenshot_failure(page, company_id, "submit_disabled")
            result.update(status="KO", message="bouton 'Envoyer' toujours désactivé après saisie")
            return result

        await submit.click()
        await asyncio.sleep(2)

        approved = page.locator(SEL_APPROVED_DOMAIN).filter(has_text=domain)
        try:
            await approved.wait_for(state="visible", timeout=8000)
        except PlaywrightTimeout:
            await _screenshot_failure(page, company_id, "verify")
            result.update(status="KO", message="domaine non confirmé dans la liste après envoi")
            return result

        print(f"  ✓ [{company_id}] Domaine '{domain}' ajouté et confirmé")
        result.update(status="OK", message="ajouté")

    except PlaywrightTimeout as e:
        result.update(status="ERREUR_TIMEOUT", message=str(e)[:150])
        print(f"  ✗ [{company_id}] Timeout : {str(e)[:100]}")
        await _screenshot_failure(page, company_id, "timeout")

    except Exception as e:
        result.update(status="ERREUR", message=str(e)[:150])
        print(f"  ✗ [{company_id}] {str(e)[:100]}")
        await _screenshot_failure(page, company_id, "exception")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    domain, dry_run = get_config()

    print("Chargement du CSV...")
    df = pd.read_csv(DATA_SOURCE, dtype=str)
    df.columns = df.columns.str.strip().str.lower()

    if "id" not in df.columns:
        print("⚠ Colonne 'id' introuvable dans le CSV — arrêt.")
        return

    df = df.dropna(subset=["id"])

    if TEST_MODE:
        df = df.head(1)
        print("[MODE TEST] 1 seule ligne traitée.\n")
    else:
        print(f"[PROD] {len(df)} ID(s) à traiter.\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not TEST_MODE)
        context = await browser.new_context(storage_state=load_session("session.json"))
        page    = await context.new_page()

        for _, row in df.iterrows():
            company_id = str(row["id"]).strip()
            print(f"\n→ Traitement {company_id}")
            result = await process_company(page, company_id, domain, dry_run)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "domain", "status", "message"])
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
