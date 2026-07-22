"""
Script de test de connexion SSO Solvimon.
Permet de vérifier que le flow OKTA fonctionne avant de lancer un vrai script.
"""
import asyncio
from playwright.async_api import async_playwright
from base import login_and_get_page


async def main():
    async with async_playwright() as p:
        browser, context, page = await login_and_get_page(p)
        print(f"✓ Connecté — URL : {page.url}")
        await context.close()
        await browser.close()
        print("✓ Test de connexion terminé.")


asyncio.run(main())
