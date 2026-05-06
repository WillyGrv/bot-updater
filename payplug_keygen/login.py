"""
À lancer UNE SEULE FOIS pour sauvegarder la session PayPlug.
Relancer uniquement si la session expire.
"""
import asyncio
from playwright.async_api import async_playwright

LOGIN_URL    = "https://portal.payplug.com/"
SESSION_FILE = "session.json"
TIMEOUT_MS   = 300_000   # 5 minutes max pour se connecter


async def save_session():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()

        await page.goto(LOGIN_URL)

        print("─────────────────────────────────────────────")
        print("Connecte-toi manuellement dans le navigateur.")
        print("─────────────────────────────────────────────")

        try:
            # Mode terminal : appui sur Entrée
            input("Appuie sur Entrée ici quand tu es connecté... ")
        except EOFError:
            # Mode dashboard (subprocess sans stdin) :
            # attend que l'URL sorte de la page de login
            print("Mode dashboard détecté — en attente de connexion (5 min max)...")
            await page.wait_for_url(
                lambda url: "login" not in url.lower() and url != LOGIN_URL,
                timeout=TIMEOUT_MS,
            )
            print("✓ Connexion détectée")

        await context.storage_state(path=SESSION_FILE)
        print(f"✓ Session sauvegardée dans {SESSION_FILE}")

        await browser.close()


asyncio.run(save_session())
