"""
Module partagé — gestion de la connexion SSO Solvimon.
Chaque script l'importe pour obtenir une page authentifiée.
"""
import asyncio
from playwright.async_api import async_playwright

SSO_URL       = "https://desk.solvimon.com/sso"
LOGIN_TIMEOUT = 300_000  # 5 min max pour compléter le flow SSO/OKTA


def _is_desk(url: str) -> bool:
    return "desk.solvimon.com" in url and "/sso" not in url and "okta" not in url


async def login_and_get_page(playwright):
    """
    Ouvre Chromium, navigue vers le SSO et attend la connexion OKTA.
    Retourne (browser, context, page) une fois connecté.
    """
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page    = await context.new_page()

    print("─────────────────────────────────────────")
    print("Connexion SSO requise.")
    print("Connecte-toi via OKTA dans le navigateur.")
    print("─────────────────────────────────────────")

    try:
        await page.goto(SSO_URL, wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass

    try:
        # Mode terminal : l'utilisateur appuie sur Entrée
        input("Appuie sur Entrée ici quand tu es connecté... ")
    except EOFError:
        # Mode dashboard : attend que le flow SSO soit terminé automatiquement
        print("Mode dashboard — en attente de connexion SSO (5 min max)...")
        await page.wait_for_url(_is_desk, timeout=LOGIN_TIMEOUT)
        await page.wait_for_load_state("networkidle", timeout=10000)

    print("✓ Connexion SSO détectée — démarrage du script\n")
    return browser, context, page
