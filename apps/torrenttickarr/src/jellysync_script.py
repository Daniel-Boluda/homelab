import json
import requests
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuración
JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')

# Puedes tener tantos grupos como quieras
JELLYFIN_SYNC_GROUPS = json.loads(os.getenv('JELLYFIN_SYNC_GROUPS'))

HEADERS = {
    "X-Emby-Token": JELLYFIN_API_KEY,
    "Accept": "application/json"
}

# ==== FUNCIONES ====

def obtener_progreso_items(user_id):
    url = f"{JELLYFIN_URL}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Episode,Movie",
        "Fields": "UserData"
    }
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    items = r.json().get("Items", [])
    progreso = {}
    for item in items:
        item_id = item["Id"]
        user_data = item.get("UserData", {})
        played = user_data.get("Played", False)
        play_pos = int(user_data.get("PlaybackPositionTicks", 0))
        runtime = int(item.get("RunTimeTicks", 1))  # evitar división por cero
        progreso[item_id] = {
            "played": played,
            "position": play_pos,
            "runtime": runtime
        }
    return progreso

def marcar_como_visto(user_id, item_id):
    url = f"{JELLYFIN_URL}/Users/{user_id}/PlayedItems/{item_id}"
    r = requests.post(url, headers=HEADERS)
    if r.status_code not in [200, 204]:
        raise Exception(f"Error marcando ítem {item_id} como visto para el usuario {user_id}")

def actualizar_progreso(user_id, item_id, position_ticks):
    url = f"{JELLYFIN_URL}/Users/{user_id}/Items/{item_id}/UserData"
    data = {
        "PlaybackPositionTicks": position_ticks
    }
    r = requests.post(url, headers=HEADERS, json=data)
    if r.status_code not in [200, 204]:
        raise Exception(f"Error actualizando progreso de {item_id} para {user_id}: {r.text}")

def sincronizar_grupo(nombre_grupo, usuarios):
    print(f"\n🔄 Sincronizando grupo: {nombre_grupo}")
    historial = {user_id: obtener_progreso_items(user_id) for user_id in usuarios}
    todos_ids = set()
    for datos in historial.values():
        todos_ids.update(datos.keys())

    for item_id in todos_ids:
        # Reunir datos del ítem por usuario
        estados = [historial[u].get(item_id, {"played": False, "position": 0, "runtime": 1}) for u in usuarios]
        alguno_visto = any(e["played"] for e in estados)
        mayor_posicion = max(e["position"] for e in estados)
        duracion = max(e["runtime"] for e in estados)

        if alguno_visto:
            # Si al menos uno lo ha visto completo, marcar como visto para todos
            for user_id in usuarios:
                if not historial[user_id].get(item_id, {}).get("played", False):
                    try:
                        marcar_como_visto(user_id, item_id)
                        print(f"✅ Usuario {user_id}: marcado como visto {item_id}")
                    except Exception as e:
                        print(f"❌ Usuario {user_id}: error al marcar visto {item_id}: {e}")
        elif mayor_posicion > 0:
            # Si hay progreso parcial, sincronizarlo al resto
            for user_id in usuarios:
                actual_pos = historial[user_id].get(item_id, {}).get("position", 0)
                if actual_pos < mayor_posicion:
                    try:
                        actualizar_progreso(user_id, item_id, mayor_posicion)
                        print(f"📼 Usuario {user_id}: progreso actualizado {item_id} a {mayor_posicion / 10**7:.2f}s")
                    except Exception as e:
                        print(f"❌ Usuario {user_id}: error al actualizar progreso {item_id}: {e}")

# ==== EJECUCIÓN ====

def sincronizar_todos_los_grupos():
    for nombre_grupo, usuarios in JELLYFIN_SYNC_GROUPS.items():
        sincronizar_grupo(nombre_grupo, usuarios)

if __name__ == "__main__":
    sincronizar_todos_los_grupos()
