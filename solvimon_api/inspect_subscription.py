"""
Étape préalable : inspecter la subscription source.
Lance ce script UNE FOIS pour voir les champs exacts retournés par l'API,
puis ajuste le payload dans solvimon_bulk_subscriptions.py si nécessaire.
"""
import os
import requests
import json

API_KEY  = os.getenv("SOLVIMON_API_KEY", "YOUR_API_KEY_HERE")
SUB_ID   = os.getenv("SOLVIMON_SUB_ID",  "YOUR_SUBSCRIPTION_ID_HERE")
BASE_URL = "https://test.api.solvimon.com/v1"



# 2. Récupérer une subscription précise (une fois le bon ID trouvé ci-dessus)
print("\n=== DÉTAIL DE LA SUBSCRIPTION SOURCE ===")
r = requests.get(
    f"{BASE_URL}/pricing-plan-subscriptions/{SUB_ID}",
    headers={"X-API-KEY": API_KEY},
)
print(f"Status HTTP : {r.status_code}")
print(json.dumps(r.json(), indent=2))
