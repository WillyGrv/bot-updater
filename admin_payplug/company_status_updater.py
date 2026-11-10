"""
CompanyStatusBot
────────────────
Pour chaque ID de input/data.csv, sur la fiche entreprise (Prod ou Sandbox selon le
choix fait avant le lancement) :
  1. Clique sur « Éditer » (#change).
  2. Sélectionne le statut choisi dans le menu « Company status ».
  3. Clique sur « Enregistrer les modifications ».

Seuls les statuts réellement autorisés en écriture sont proposés (Enregistré, Bloqué,
Désactivé) — "Vérifié" et "Auto-bloqué" sont exclus car la page admin les marque
elle-même "You're not allowed to set this status".

Environnement (choisi avant le lancement) :
  - Production : fonctionnement classique, réutilise session.json (admin.payplug.com).
  - Sandbox    : ignore session.json, ouvre un navigateur visible sur
                 https://admin-qa.payplug.com/admin/login. Deux façons de se connecter :
                   • identifiants email + mot de passe fournis (mêmes champs qu'en Prod,
                     sans passer par Okta) → tentative de connexion automatique ;
                   • sinon, connexion manuelle dans le navigateur (SSO Okta, y compris
                     la MFA, ou saisie manuelle email + mot de passe) puis confirmation
                     explicite (pas de détection automatique de redirection).
                 Okta redirige normalement vers staging-internal-payment.payplug.com
                 après authentification (atterrissage du SSO, rien à faire dessus) —
                 le script navigue ensuite explicitement vers
                 admin-qa.payplug.com/admin/companies/{id} pour traiter chaque fiche.

                 Session Sandbox persistée dans sandbox_session.json : si elle existe
                 et semble encore active (vérifiée en visitant la page de login — un
                 compte déjà authentifié n'y reste pas), la connexion est entièrement
                 sautée. Sinon (première fois, ou session expirée), connexion normale
                 puis sauvegarde pour les prochains runs. Pour forcer une reconnexion,
                 supprime simplement ce fichier.

Lancement (terminal) :
    python company_status_updater.py
Ou depuis le dashboard (bouton "CompanyStatusBot" du bot Admin PayPlug).
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
LOG_FILE    = f"results/results_company_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
TEST_MODE = False

ENVIRONMENTS = {
    "prod": {
        "label":     "Production — admin.payplug.com",
        "base_url":  "https://admin.payplug.com/admin/companies/{id}",
        "login_url": None,
    },
    "sandbox": {
        "label":     "Sandbox — admin-qa.payplug.com",
        # Le SSO Okta démarré sur admin-qa.payplug.com/admin/login redirige, une fois
        # authentifié, vers staging-internal-payment.payplug.com (normal, c'est juste
        # l'atterrissage du SSO) — mais les fiches entreprise à traiter restent bien
        # sur admin-qa.payplug.com, où le script navigue explicitement ensuite.
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
# ───────────────────────────────────────────────────────────────────────────────


def get_environment() -> str:
    """Priorité : paramètre dashboard (stdin), sinon prompt manuel. Défaut : prod."""
    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("ENVIRONNEMENT")
        print("─────────────────────────────────────────────")
        print("  [1] Production (admin.payplug.com) — session habituelle")
        print("  [2] Sandbox (admin-qa.payplug.com) — nouveau login demandé")
        raw = input("  Choix (défaut: 1) : ").strip()
        print("─────────────────────────────────────────────\n")
        return "sandbox" if raw == "2" else "prod"

    try:
        raw = input().strip().lower()
    except EOFError:
        raw = ""
    return raw if raw in ENVIRONMENTS else "prod"


def get_target_status() -> tuple[str, str]:
    """Priorité : paramètre dashboard (stdin, value brute), sinon prompt manuel.
    Défaut : premier statut de la liste (Enregistré)."""
    items = list(STATUS_OPTIONS.items())

    if sys.stdin.isatty():
        print("─────────────────────────────────────────────")
        print("STATUT CIBLE (Company status)")
        print("─────────────────────────────────────────────")
        for i, (value, label) in enumerate(items, start=1):
            print(f"  [{i}] {label}")
        raw = input(f"  Choix (défaut: 1) : ").strip()
        print("─────────────────────────────────────────────\n")
        idx = int(raw) - 1 if raw.isdigit() and 1 <= int(raw) <= len(items) else 0
        return items[idx]

    try:
        raw = input().strip()
    except EOFError:
        raw = ""
    if raw in STATUS_OPTIONS:
        return raw, STATUS_OPTIONS[raw]
    return items[0]


def get_sandbox_credentials():
    """Lit {"login", "password", "name"} sur stdin (paramètre dashboard "credentials",
    pertinent uniquement en Sandbox). Toujours lu — même en Production — pour garder
    l'alignement des lignes stdin avec les autres paramètres. None si absent/incomplet/
    terminal : dans ce cas, connexion manuelle (SSO Okta ou email+mdp saisis à la main)."""
    if sys.stdin.isatty():
        return None
    try:
        raw = input().strip()
    except EOFError:
        return None
    if not raw:
        return None
    try:
        creds = json.loads(raw)
    except Exception:
        return None
    login    = (creds.get("login") or "").strip()
    password = (creds.get("password") or "").strip()
    return (login, password) if login and password else None


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


SANDBOX_APP_DOMAIN   = "staging-internal-payment.payplug.com"
SANDBOX_SESSION_FILE = "sandbox_session.json"


def load_sandbox_session():
    """Charge sandbox_session.json s'il existe (session Okta sauvegardée d'un run
    précédent). None si absent/illisible — dans ce cas, connexion normale requise."""
    if not os.path.exists(SANDBOX_SESSION_FILE):
        return None
    try:
        return load_session(SANDBOX_SESSION_FILE)
    except Exception:
        return None


async def sandbox_session_still_valid(page, login_url: str) -> bool:
    """Vérifie qu'une session Sandbox chargée depuis sandbox_session.json est encore
    active : si on retombe sur /login ou sur okta.com en visitant login_url, la
    session Okta a expiré (sinon, un compte déjà authentifié est en général renvoyé
    directement vers l'application, sans repasser par le formulaire)."""
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)  # laisser une éventuelle redirection se terminer
    except Exception:
        return False
    low = page.url.lower()
    return "okta" not in low and "/login" not in low

# Mêmes champs que le formulaire de login classique (login.py / admin.payplug.com) —
# utilisés uniquement si des identifiants email + mot de passe sont fournis, en
# alternative au SSO Okta.
SEL_LOGIN_EMAIL    = 'input[name="email"]'
SEL_LOGIN_PASSWORD = 'input[name="password"]'
SEL_LOGIN_SUBMIT   = 'input[type="submit"][name="admin_login"]'


async def try_sandbox_auto_login(page, login: str, password: str) -> bool:
    print("─────────────────────────────────────────")
    print("Identifiants fournis — tentative de connexion email + mot de passe (sans Okta)...")
    print("─────────────────────────────────────────")
    try:
        await page.fill(SEL_LOGIN_EMAIL, login, timeout=8000)
        await page.fill(SEL_LOGIN_PASSWORD, password, timeout=8000)
        await page.locator(SEL_LOGIN_SUBMIT).first.click(timeout=8000)
        await page.wait_for_url(lambda url: "login" not in url.lower(), timeout=20000)
        print("✓ Connexion automatique réussie")
        return True
    except Exception as e:
        print(f"⚠ Connexion automatique impossible ({str(e)[:100]}) — bascule en connexion manuelle (SSO Okta ou email+mdp)")
        return False


async def sandbox_login(page, login_url: str, credentials) -> None:
    """Connexion Sandbox (jamais de session.json réutilisée ici) — deux chemins :
    1. Identifiants email + mot de passe fournis → tentative de connexion automatique
       (mêmes champs qu'en Prod, sans passer par Okta).
    2. Sinon (ou si l'auto-login échoue) → connexion manuelle dans le navigateur (SSO
       Okta, y compris la MFA — atterrit normalement sur staging-internal-payment.
       payplug.com — ou saisie manuelle email + mot de passe), puis confirmation
       explicite (pas de détection automatique de redirection) : terminal → touche
       Entrée ; dashboard → bouton Oui/Non via le marqueur ###CONFIRM_REQUIRED###
       (voir "interactive" dans dashboard.py — même mécanisme que
       solvimon_bulk_subscriptions.py)."""
    print("─────────────────────────────────────────")
    print(f"ENVIRONNEMENT SANDBOX — connexion requise sur {login_url}")
    print("─────────────────────────────────────────")
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    if credentials:
        login, password = credentials
        if await try_sandbox_auto_login(page, login, password):
            print("✓ Connexion confirmée — poursuite du script.\n")
            return

    print(f"\n⚠ Connecte-toi dans la fenêtre Chromium ouverte — via le SSO Okta (y compris la MFA, "
          f"redirection normale vers {SANDBOX_APP_DOMAIN}) ou en saisissant email + mot de passe "
          "directement dans le formulaire.")
    print("Une fois connecté, confirme ci-dessous — le script ira ensuite directement sur")
    print("admin-qa.payplug.com pour traiter chaque fiche entreprise.")
    print("###CONFIRM_REQUIRED###")
    try:
        typed = input("  Connexion Sandbox effectuée, continuer ? (o/N) : ").strip().lower()
    except EOFError:
        typed = ""

    if typed not in ("o", "oui", "y", "yes"):
        print("⚠ Connexion non confirmée — arrêt sans rien faire.")
        sys.exit(1)

    print("✓ Connexion confirmée — poursuite du script.\n")


async def process_company(page, company_id: str, base_url: str, status_value: str, status_label: str) -> dict:
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
            await _screenshot_failure(page, company_id, "status_precheck")
            result["result"]  = "KO"
            result["message"] = f"valeur sélectionnée '{confirmed}' ≠ '{status_value}' avant envoi"
            return result

        await page.click(save_edit)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await asyncio.sleep(1)

        # ── Contrôle après enregistrement (relecture de la page) ─────────────────
        final_value = await page.eval_on_selector(SEL_STATUS_SELECT, "el => el.value")
        if final_value != status_value:
            await _screenshot_failure(page, company_id, "status_postcheck")
            result["result"]  = "KO"
            result["message"] = f"statut après sauvegarde '{final_value}' ≠ '{status_value}' attendu"
            return result

        print(f"  ✓ [{company_id}] Company status → {status_label}")
        result["result"]  = "OK"
        result["message"] = "statut mis à jour"

    except PlaywrightTimeout as e:
        result["result"]  = "ERREUR_TIMEOUT"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_id}] Timeout : {str(e)[:80]}")
        await _screenshot_failure(page, company_id, "timeout")

    except Exception as e:
        result["result"]  = "ERREUR"
        result["message"] = str(e)[:120]
        print(f"  ✗ [{company_id}] {str(e)[:80]}")
        await _screenshot_failure(page, company_id, "erreur")

    return result


async def main():
    os.makedirs("results", exist_ok=True)

    environment = get_environment()
    env_cfg     = ENVIRONMENTS[environment]
    print(f"Environnement : {env_cfg['label']}\n")

    status_value, status_label = get_target_status()
    print(f"Statut cible : {status_label} (value={status_value})\n")

    # Toujours lu (même en Production) pour garder l'alignement des lignes stdin —
    # n'est utilisé que si environment == "sandbox".
    sandbox_credentials = get_sandbox_credentials()

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

    results  = []
    headless = (environment == "prod") and not TEST_MODE

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        if environment == "sandbox":
            saved_state = load_sandbox_session()
            context = await browser.new_context(storage_state=saved_state) if saved_state else await browser.new_context()
            page    = await context.new_page()

            session_ok = False
            if saved_state:
                print(f"Session Sandbox existante trouvée ({SANDBOX_SESSION_FILE}) — vérification...")
                session_ok = await sandbox_session_still_valid(page, env_cfg["login_url"])
                print("✓ Session Sandbox toujours active — connexion ignorée.\n" if session_ok
                      else "⚠ Session Sandbox expirée ou invalide — nouvelle connexion requise.\n")

            if not session_ok:
                await sandbox_login(page, env_cfg["login_url"], sandbox_credentials)
                await context.storage_state(path=SANDBOX_SESSION_FILE)
                print(f"✓ Session Sandbox sauvegardée → {SANDBOX_SESSION_FILE} (réutilisée au prochain run tant qu'elle reste valide)\n")
        else:
            context = await browser.new_context(storage_state=load_session("session.json"))
            page    = await context.new_page()

        for _, row in df.iterrows():
            company_id = str(row["id"]).strip()
            print(f"\n→ Traitement {company_id}")
            result = await process_company(page, company_id, env_cfg["base_url"], status_value, status_label)
            results.append(result)
            await asyncio.sleep(1.0)

        await context.close()
        await browser.close()

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "status_cible", "result", "message"])
        writer.writeheader()
        writer.writerows(results)

    ok  = sum(1 for r in results if r["result"] == "OK")
    err = len(results) - ok
    print(f"\n─────────────────────────────")
    print(f"✓ Succès  : {ok}")
    print(f"✗ Erreurs : {err}")
    print(f"Log sauvegardé → {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
