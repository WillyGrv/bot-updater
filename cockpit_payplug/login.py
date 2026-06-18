"""
À lancer UNE SEULE FOIS pour sauvegarder la session Cockpit.
Relancer uniquement si la session expire.
"""
import asyncio
from playwright.async_api import async_playwright

LOGIN_URL    = "https://internal-payment.gcp.dlns.io/cockpit/public/#/login"
SESSION_FILE = "session.json"
TIMEOUT_MS   = 300_000   # 5 minutes max pour se connecter


async def save_session():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()

        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

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

        await context.storage_state(path=SESSION_FILE)
        print(f"✓ Session sauvegardée dans {SESSION_FILE}")

        await browser.close()


asyncio.run(save_session())
