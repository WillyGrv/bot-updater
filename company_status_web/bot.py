"""
Logique Playwright de CompanyStatusBot — version "web", sans navigateur visible.

Adaptée de admin_payplug/company_status_updater.py, avec deux différences assumées :
  - Pas de mode terminal, pas de confirmation interactive (###CONFIRM_REQUIRED###) —
    ce module tourne headless sur un serveur, personne ne regarde l'écran.
  - Sandbox : connexion automatique email + mot de passe UNIQUEMENT (pas de SSO Okta
    ni de MFA possibles sans navigateur visible). Si le compte Sandbox impose la MFA,
    ce flux échouera — utiliser le script local dans ce cas, ou importer un
    sandbox_session.json déjà authentifié exporté depuis une session locale valide.
"""
from __future__ import annotations

import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

ENVIRONMENTS = {
    "prod": {
        "base_url":  "https://admin.payplug.com/admin/companies/{id}",
        "login_url": None,
    },
    "sandbox": {
        "base_url":  "https://admin-qa.payplug.com/admin/companies/{id}",
        "login_url": "https://admin-qa.payplug.com/admin/login",
    },
}

# Exclut volontairement value=4 (Vérifié) et value=8 (Auto-bloqué), marqués
# "You're not allowed to set this status" sur la page admin.
STATUS_OPTIONS = {
    "2": "Enregistré",
    "5": "Bloqué",
    "6": "Désactivé",
}

EDIT_BUTTON       = "#change"
SAVE_BUTTON       = "input[type='submit'].main-submit"
SEL_STATUS_SELECT = "select#status"

SEL_LOGIN_EMAIL    = 'input[name="email"]'
SEL_LOGIN_PASSWORD = 'input[name="password"]'
SEL_LOGIN_SUBMIT   = 'input[type="submit"][name="admin_login"]'


async def try_login(page, login: str, password: str, log) -> bool:
    log("Tentative de connexion email + mot de passe...")
    try:
        await page.fill(SEL_LOGIN_EMAIL, login, timeout=8000)
        await page.fill(SEL_LOGIN_PASSWORD, password, timeout=8000)
        await page.locator(SEL_LOGIN_SUBMIT).first.click(timeout=8000)
        await page.wait_for_url(lambda url: "login" not in url.lower(), timeout=20000)
        log("✓ Connexion réussie")
        return True
    except Exception as e:
        log(f"✗ Connexion impossible ({str(e)[:150]}) — si ce compte impose une MFA/Okta, "
            f"ce flux web ne peut pas aboutir : utilise le script local dans ce cas.")
        return False


async def process_company(page, company_id: str, base_url: str, status_value: str, status_label: str, log) -> dict:
    result = {"id": company_id, "status_cible": status_label, "result": "", "message": ""}
    url = base_url.format(id=company_id)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        await page.wait_for_selector(EDIT_BUTTON, timeout=10000)
        await page.click(EDIT_BUTTON)

        save_edit = f"{SAVE_BUTTON}:not([readonly])"
        await page.wait_for_selector(save_edit, timeout=10000)

        await page.wait_for_selector(SEL_STATUS_SELECT, timeout=10000)
        select = page.locator(SEL_STATUS_SELECT)
        await select.select_option(value=status_value)

        confirmed = await page.eval_on_selector(SEL_STATUS_SELECT, "el => el.value")
        if confirmed != status_value:
            result["result"]  = "KO"
            result["message"] = f"valeur sélectionnée '{confirmed}' ≠ '{status_value}' avant envoi"
            return result

        await page.click(save_edit)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await asyncio.sleep(1)

        final_value = await page.eval_on_selector(SEL_STATUS_SELECT, "el => el.value")
        if final_value != status_value:
            result["result"]  = "KO"
            result["message"] = f"statut après sauvegarde '{final_value}' ≠ '{status_value}' attendu"
            return result

        log(f"✓ [{company_id}] Company status → {status_label}")
        result["result"]  = "OK"
        result["message"] = "statut mis à jour"

    except PlaywrightTimeout as e:
        result["result"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:150]
        log(f"✗ [{company_id}] Timeout : {str(e)[:120]}")

    except Exception as e:
        result["result"]  = "ERREUR"
        result["message"] = str(e)[:150]
        log(f"✗ [{company_id}] {str(e)[:120]}")

    return result


async def run_job(
    environment: str,
    status_value: str,
    company_ids: list[str],
    credentials: tuple[str, str] | None,
    sandbox_storage_state: dict | None,
    prod_storage_state: dict | None,
    log,
) -> tuple[list[dict], dict | None]:
    """Exécute le run complet. Retourne (results, new_sandbox_storage_state) —
    new_sandbox_storage_state n'est renseigné qu'en Sandbox, pour mise en cache
    côté appelant (fichier et/ou téléchargement)."""
    env_cfg      = ENVIRONMENTS[environment]
    status_label = STATUS_OPTIONS.get(status_value, status_value)
    results: list[dict] = []
    new_sandbox_state = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        if environment == "sandbox":
            context = (
                await browser.new_context(storage_state=sandbox_storage_state)
                if sandbox_storage_state else await browser.new_context()
            )
            page = await context.new_page()

            session_ok = False
            if sandbox_storage_state:
                log("Session Sandbox fournie — vérification...")
                try:
                    await page.goto(env_cfg["login_url"], wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(1)
                    low = page.url.lower()
                    session_ok = "okta" not in low and "/login" not in low
                except Exception:
                    session_ok = False
                log("✓ Session Sandbox active — connexion ignorée" if session_ok
                    else "⚠ Session Sandbox invalide ou expirée")

            if not session_ok:
                if not credentials:
                    log("✗ Aucun identifiant fourni et pas de session valide — arrêt "
                        "(le SSO Okta / la MFA ne peuvent pas être gérés sans navigateur visible ici).")
                    await context.close()
                    await browser.close()
                    return [], None

                login, password = credentials
                try:
                    await page.goto(env_cfg["login_url"], wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                if not await try_login(page, login, password, log):
                    await context.close()
                    await browser.close()
                    return [], None

            new_sandbox_state = await context.storage_state()
        else:
            if not prod_storage_state:
                log("✗ session.json Production requis mais non fourni — arrêt.")
                await browser.close()
                return [], None
            context = await browser.new_context(storage_state=prod_storage_state)
            page    = await context.new_page()

        for company_id in company_ids:
            log(f"→ Traitement {company_id}")
            result = await process_company(page, company_id, env_cfg["base_url"], status_value, status_label, log)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    return results, new_sandbox_state
