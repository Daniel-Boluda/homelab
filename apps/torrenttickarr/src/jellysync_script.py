import requests
import os
from dotenv import load_dotenv

# Configuración
JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_URL')

# Puedes tener tantos grupos como quieras
JELLYFIN_SYNC_GROUPS= os.getenv('JELLYFIN_SYNC_GROUPS')

HEADERS = {
    "X-Emby-Token": JELLYFIN_API_KEY,
    "Accept": "application/json"
}

# ==== FUNCIONES ====

def obtener_items_vistos(user_id):
    url = f"{JELLYFIN_URL}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "Filters": "IsPlayed",
        "IncludeItemTypes": "Episode,Movie"
    }
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    items = r.json().get("Items", [])
    return set(item['Id'] for item in items)

def marcar_como_visto(user_id, item_id):
    url = f"{JELLYFIN_URL}/Users/{user_id}/PlayedItems/{item_id}"
    r = requests.post(url, headers=HEADERS)
    if r.status_code not in [200, 204]:
        raise Exception(f"Error marcando ítem {item_id} como visto para el usuario {user_id}")

def sincronizar_grupo(nombre_grupo, usuarios):
    print(f"\n🔄 Sincronizando grupo: {nombre_grupo}")
    historial = {}
    union_vistos = set()

    # Paso 1: obtener historial de todos los usuarios del grupo
    for user_id in usuarios:
        vistos = obtener_items_vistos(user_id)
        historial[user_id] = vistos
        union_vistos.update(vistos)
        print(f" - Usuario {user_id}: {len(vistos)} ítems vistos")

    # Paso 2: marcar ítems que faltan en cada usuario
    for user_id in usuarios:
        faltan = union_vistos - historial[user_id]
        print(f" → Usuario {user_id} le faltan {len(faltan)} ítems")
        for i, item_id in enumerate(faltan, 1):
            try:
                marcar_como_visto(user_id, item_id)
                print(f"   [{i}/{len(faltan)}] Marcado como visto: {item_id}")
            except Exception as e:
                print(f"   ❌ Error: {e}")

# ==== EJECUCIÓN ====

def sincronizar_todos_los_grupos():
    for nombre_grupo, usuarios in JELLYFIN_SYNC_GROUPS.items():
        sincronizar_grupo(nombre_grupo, usuarios)

if __name__ == "__main__":
    sincronizar_todos_los_grupos()