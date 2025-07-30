import requests
import os
from datetime import datetime, timedelta
from dateutil.parser import parse as parse_date
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

# === CONFIGURACIÓN ===
JELLYFIN_URL = os.getenv('JELLYFIN_URL')
JELLYFIN_API_KEY = os.getenv('JELLYFIN_API_KEY')
JELLYFIN_USER_ID = "1" 

RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')

JELLYSEERR_URL = os.getenv('JELLYSEERR_URL')
JELLYSEERR_API_KEY = os.getenv('JELLYSEERR_API_KEY')

MESES_LIMITE = 6

# === FUNCIONES ===

def get_unwatched_items_via_prp():
    url = f"{JELLYFIN_URL}/user_usage_stats/submit_custom_query"
    headers = {"Authorization": f"MediaBrowser Token={JELLYFIN_API_KEY}",
               "accept": "application/json",
               "Content-Type": "application/json"}
    # Consulta SQL para identificar Items nunca vistos
    payload = {
        "CustomQueryString": """
            SELECT ItemId
            FROM PlaybackActivity
            GROUP BY ItemId
        """,
        "ReplaceUserId": True
    }
    r = requests.post(url, headers=headers, json=payload)
    r.raise_for_status()
    results = r.json().get("results", [])
    return {row[0] for row in results}  # column ItemId asumida

def get_unwatched_movies_global():
    unwatched_ids = get_unwatched_items_via_prp()
    # Obtener todos los movies y luego filtrar por esos IDs
    url = f"{JELLYFIN_URL}/Items"
    headers = {"Authorization": f"MediaBrowser Token={JELLYFIN_API_KEY}"}
    params = {
        "IncludeItemTypes": "Movie", "Recursive": "true",
        "Fields": "ProviderIds,DateCreated,UserData"
    }
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    items = r.json().get("Items", [])
    return [item for item in items if item["Id"] in unwatched_ids]

def get_radarr_movies():
    headers = {"X-Api-Key": RADARR_API_KEY}
    url = f"{RADARR_URL}/api/v3/movie"
    resp = requests.get(url, headers=headers).json()
    return resp

def get_jellyseerr_requests():
    headers = {"X-Api-Key": JELLYSEERR_API_KEY}
    url = f"{JELLYSEERR_URL}/api/v1/request"
    resp = requests.get(url, headers=headers).json()
    return resp.get("results", [])

def cruzar_datos(jellyfin_unwatched, radarr_movies, jellyseerr_requests):
    resultados = []

    radarr_by_tmdb = {m["tmdbId"]: m for m in radarr_movies}
    jellyseerr_by_tmdb = {r["media"]["tmdbId"]: r for r in jellyseerr_requests}

    ahora = datetime.now()
    limite_fecha = ahora - timedelta(days=MESES_LIMITE * 30)

    for item in jellyfin_unwatched:
        title = item.get("Name")
        tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
        date_created = parse_date(item.get("DateCreated"))

        if not tmdb_id or tmdb_id not in radarr_by_tmdb:
            continue  # No está gestionada por Radarr

        if date_created > limite_fecha:
            continue  # No es suficientemente antigua

        radarr_data = radarr_by_tmdb[tmdb_id]
        added_date = parse_date(radarr_data.get("added"))
        monitored = radarr_data.get("monitored")
        via_list = radarr_data.get("addOptions", {}).get("searchForMovie", False)

        seerr_info = jellyseerr_by_tmdb.get(tmdb_id)
        requested_by = seerr_info["requestedBy"][0]["displayName"] if seerr_info else "No solicitado"
        via_auto_list = "Lista automática" if not via_list else "Manual"

        resultados.append({
            "titulo": title,
            "fecha_radarr": added_date.date(),
            "fecha_jellyfin": date_created.date(),
            "solicitado_por": requested_by,
            "monitoreado": monitored,
            "fuente": via_auto_list
        })

    return resultados

# === EJECUCIÓN ===

jellyfin_unwatched = get_unwatched_movies_global()
radarr_movies = get_radarr_movies()
jellyseerr_requests = get_jellyseerr_requests()

resultados = cruzar_datos(jellyfin_unwatched, radarr_movies, jellyseerr_requests)

print("Películas no vistas y antiguas:\n")
for r in resultados:
    print(f"- {r['titulo']} | Añadida: {r['fecha_radarr']} | Solicitado por: {r['solicitado_por']} | Fuente: {r['fuente']}")
