"""
À lancer UNE SEULE FOIS pour sauvegarder la session Cockpit.
Relancer uniquement si la session expire.

Optionnel : si des identifiants sont fournis sur stdin
(JSON {"login": "...", "password": "...", "name": "..." ou null}, envoyés par le
dashboard), une connexion automatique est tentée et le nom — s'il y en a un — est
mémorisé dans session_owner.json pour affichage dans le dashboard. En cas d'échec —
ou si rien n'est fourni — on retombe sur la connexion manuelle habituelle.
"""
import asyncio
import json
import os
import sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

LOGIN_URL    = "https://internal-payment.gcp.dlns.io/cockpit/public/#/login"
SESSION_FILE = "session.json"
OWNER_FILE   = "session_owner.json"
TIMEOUT_MS   = 300_000   # 5 minutes max pour se connecter

SEL_USERNAME = 'input[placeholder="Email or Username"]'
SEL_PASSWORD = 'input[type="password"][placeholder="Password"]'
SEL_SUBMIT   = 'button[type="submit"]'


def read_credentials():
    """Lit {"login", "password", "name"} sur stdin (mode dashboard). None si absent/incomplet."""
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.readline().strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        creds = json.loads(raw)
    except Exception:
        return None
    login    = (creds.get("login") or "").strip()
    password = (creds.get("password") or "").strip()
    name     = (creds.get("name") or "").strip() or None
    return (login, password, name) if login and password else None


def write_session_owner(name):
    """Mémorise (ou efface) le nom de l'identifiant utilisé pour cette session."""
    if name:
        with open(OWNER_FILE, "w", encoding="utf-8") as f:
            json.dump({"name": name}, f, ensure_ascii=False)
    else:
        try:
            os.remove(OWNER_FILE)
        except FileNotFoundError:
            pass


async def try_auto_login(page, login: str, password: str) -> bool:
    print("─────────────────────────────────────────")
    print("Identifiants fournis — tentative de connexion automatique...")
    print("─────────────────────────────────────────")
    try:
        await page.fill(SEL_USERNAME, login, timeout=8000)
        await page.fill(SEL_PASSWORD, password, timeout=8000)
        await page.locator(SEL_SUBMIT).first.click(timeout=8000)
        await page.wait_for_url(lambda url: "#/login" not in url.lower(), timeout=20000)
        print("✓ Connexion automatique réussie")
        return True
    except (PlaywrightTimeout, Exception) as e:
        print(f"⚠ Connexion automatique impossible ({str(e)[:100]}) — bascule en connexion manuelle")
        return False


async def manual_login(page):
    print("─────────────────────────────────────────")
    print("Connecte-toi manuellement dans le navigateur.")
    print("─────────────────────────────────────────")

    try:
        input("Appuie sur Entrée ici quand tu es connecté... ")
    except EOFError:
        print("Mode dashboard détecté — en attente de connexion (5 min max)...")
        try:
            await page.wait_for_url(
                lambda url: "#/login" not in url.lower(),
                timeout=TIMEOUT_MS,
            )
        except Exception:
            pass
        print("✓ Connexion détectée ou timeout — sauvegarde de la session")


async def save_session():
    credentials = read_credentials()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

        connected = False
        name      = None
        if credentials:
            login, password, name = credentials
            connected = await try_auto_login(page, login, password)

        if not connected:
            await manual_login(page)
            name = None

        await context.storage_state(path=SESSION_FILE)
        write_session_owner(name)
        print(f"✓ Session sauvegardée dans {SESSION_FILE}")
        if name:
            print(f"✓ Session associée aux identifiants « {name} »")

        await browser.close()


asyncio.run(save_session())
